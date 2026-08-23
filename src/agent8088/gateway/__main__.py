import asyncio
import logging


def main() -> None:
    from agent8088.logging_setup import configure_logging
    configure_logging()
    # `python -m agent8088.gateway` bypasses cli.main(), where
    # web_search_provider=auto normally resolves. Resolve here too so an
    # unattended gateway does not spend the whole session on an unresolved pin
    # (which fails closed, but would gate every search behind an approval no
    # operator is watching for). Idempotent when already resolved or pinned.
    from agent8088 import engine
    engine.resolve_auto_search_provider()

    from agent8088.gateway.runner import build_runner
    runner = build_runner()
    if not runner.adapters:
        logging.error("No messaging platforms enabled. Set one of slack_enabled, whatsapp_enabled, discord_enabled, email_enabled, or telegram_enabled to 1 in config.txt (or run: agent8088 --gateway-setup).")
        return
    try:
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        logging.info("Gateway stopped.")


if __name__ == "__main__":
    main()