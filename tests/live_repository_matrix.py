"""Opt-in real provider + tool verification; key is read without echo."""
import getpass
import json
from pathlib import Path
import time
import os
from agent8088 import engine as a


def main():
    key = getpass.getpass('API key: ')
    a.PROVIDERS['ollama-cloud'].update(api_key=key, api_key_env='')
    a.ACTIVE_PROVIDER = 'ollama-cloud'
    a.client, _ = a.get_client('ollama-cloud')
    a.MODEL_NAME = 'glm-5.3'
    a.PERMISSION_MODE = 'full-auto'
    cases = [
        ('local-search', 'Use repository_read on tests with include test_repository_sparse.py. Search for test_pack_rename_during_budget_scan, then read the file and explain what the test checks in two sentences.'),
        ('private-nested', 'Use repository_read on https://github.com/palindrome-rl/AGENT8088 at revision feat/gitingest-repository-context, include docs/**. Get overview, then read docs/repository-ingestion.md using that snapshot_id and the same include. Give three documented limits. Do not change the include filter.'),
        ('large-repo', 'Use repository_read to read README.md from https://github.com/heygen-com/hyperframes and explain the repository in at most three sentences. Do not read every page if the first page explains its purpose.'),
        ('negative', 'Use repository_read to attempt https://gitlab.com/gitlab-org/gitlab once and then read nonexistent-qa-file.txt from https://github.com/octocat/Hello-World once. Explain both errors; do not retry or use other tools.'),
        ('nested-exact', 'Use repository_read with source https://github.com/palindrome-rl/AGENT8088 and revision feat/gitingest-repository-context and path docs/repository-ingestion.md and action read. No include filter. Report whether the file was readable and give one documented restriction.'),
    ]
    reports = []
    for name, prompt in cases:
        if os.environ.get('REPOSITORY_CASE') and os.environ['REPOSITORY_CASE'] != name:
            continue
        events = []
        started = time.monotonic()
        def result(tool, out):
            events.append({'tool':tool, 'result':out})
            print('TOOL', name, tool, out[:220], flush=True)
        answer = a.run_agent([{'role':'user','content':prompt}], max_turns=8,
            allowed_tools={'repository_read'}, on_result=result,
            on_calls=lambda calls: events.append({'calls':calls}))
        report = {'case':name, 'seconds':round(time.monotonic()-started,2),
                  'answer':answer, 'events':events}
        reports.append(report)
        target = Path('web/test-results/repository-matrix'+('-'+os.environ['REPOSITORY_CASE'] if os.environ.get('REPOSITORY_CASE') else '')+'.json')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(reports, indent=2), encoding='utf-8')
        print('CASE', name, 'ANSWER', answer, flush=True)


if __name__ == '__main__':
    main()
