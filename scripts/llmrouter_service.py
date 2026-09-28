"""Run in a separate environment with llmrouter-lib installed; see docs/llmrouter.md."""
import argparse
import contextlib
import hmac
import os
import sys
import threading

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


def create_app(router, token):
    if not token:
        raise ValueError('Set AGENT8088_ROUTER_TOKEN before starting the service')
    app = FastAPI(docs_url=None, redoc_url=None)
    lock = threading.Lock()

    class Request(BaseModel):
        query: str = Field(min_length=1, max_length=12000)
        candidates: list[str] = Field(min_length=1, max_length=64)

    @app.get('/health')
    def health():
        return {'ready': True}

    @app.post('/route')
    def route(body: Request, authorization: str = Header(default='')):
        if not hmac.compare_digest(authorization, 'Bearer ' + token):
            raise HTTPException(401, 'Unauthorized')
        # Serialize inference. Never queue an unbounded backlog behind a slow model.
        if not lock.acquire(blocking=False):
            raise HTTPException(503, 'Router busy')
        try:
            with contextlib.redirect_stdout(sys.stderr):
                result = router.route_single({'query': body.query})
            selected = result.get('model_name')
            if selected not in body.candidates:
                raise HTTPException(422, 'No eligible recommendation')
            return {'model_name': selected}
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, 'Routing failed') from None
        finally:
            lock.release()
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--router', choices=['knnrouter', 'smallest_llm', 'largest_llm'], required=True)
    parser.add_argument('--config', required=True, help='Trusted upstream YAML; use absolute artifact paths')
    parser.add_argument('--port', type=int, default=8191)
    args = parser.parse_args()
    # Import once and keep the router alive. No SDK dependency in Agent8088.
    from llmrouter.models import KNNRouter, SmallestLLM, LargestLLM
    import uvicorn
    classes = {'knnrouter': KNNRouter, 'smallest_llm': SmallestLLM, 'largest_llm': LargestLLM}
    router = classes[args.router](yaml_path=args.config)
    app = create_app(router, os.environ.get('AGENT8088_ROUTER_TOKEN', ''))
    uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
