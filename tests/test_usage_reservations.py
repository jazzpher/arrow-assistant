import datetime
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from arrow_assistant import ai, usage
from arrow_assistant.config import LLMProvider
from arrow_assistant.usage import UsageTracker, UsageLimitReached


def test_atomic_reservation_across_trackers_and_threads(tmp_path):
    path = str(tmp_path / 'usage.json')
    def reserve(_):
        t = UsageTracker(path, env={'ARROW_DAILY_REQUEST_LIMIT': '1'})
        try:
            t.reserve('gemini', 'm')
            return True
        except UsageLimitReached:
            return False
    with ThreadPoolExecutor(8) as pool:
        assert sum(pool.map(reserve, range(8))) == 1
    assert UsageTracker(path).snapshot()['gemini']['requests'] == 1


def test_atomic_reservation_across_processes(tmp_path):
    path = str(tmp_path / 'usage.json')
    code = '''import sys
from arrow_assistant.usage import UsageTracker, UsageLimitReached
try:
 UsageTracker(sys.argv[1],env={'ARROW_DAILY_REQUEST_LIMIT':'1'}).reserve('gemini','m')
 print('allowed')
except UsageLimitReached:
 print('blocked')
'''
    procs = [subprocess.Popen([sys.executable, '-c', code, path], stdout=subprocess.PIPE, text=True)
             for _ in range(4)]
    results = [p.communicate(timeout=20)[0].strip() for p in procs]
    assert results.count('allowed') == 1
    assert results.count('blocked') == 3


def test_nested_corrupt_counter_stops_and_is_not_overwritten(tmp_path):
    path = tmp_path / 'usage.json'
    raw = json.dumps({'days': {datetime.date.today().isoformat(): {'providers': [], 'alerts': []}}})
    path.write_text(raw)
    with pytest.raises(UsageLimitReached, match='corrupt'):
        UsageTracker(str(path)).reserve('gemini', 'm')
    assert path.read_text() == raw


def test_unknown_usage_blocks_token_threshold(tmp_path):
    t = UsageTracker(str(tmp_path/'u.json'), env={'ARROW_DAILY_TOKEN_LIMIT':'100'})
    t.reserve('gemini','m')
    t.finish('gemini','m',None)
    assert t.snapshot()['gemini']['unknown_usage'] == 1
    with pytest.raises(UsageLimitReached, match='unknown'):
        t.reserve('gemini','m')
    t.finish('gemini','m',(10,5,15))
    t.reserve('gemini','m')


def test_paid_openrouter_blocked_before_network(monkeypatch):
    monkeypatch.setattr(ai.requests,'post',lambda *a,**k: pytest.fail('network called'))
    with pytest.raises(UsageLimitReached,match='free-only'):
        list(ai.stream_answer(LLMProvider('openrouter','secret','paid/model'), 'q','app','',None,None))


def test_interrupted_stream_keeps_reserved_call_and_unknown_usage(monkeypatch,_isolated_usage):
    class R:
        status_code=200
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def iter_lines(self,decode_unicode=True):
            yield 'data: {"candidates":[{"content":{"parts":[{"text":"hello"}]}}]}'
            yield 'data: {"usageMetadata":{"totalTokenCount":100}}'
    monkeypatch.setattr(ai.requests,'post',lambda *a,**k:R())
    g=ai.stream_answer(LLMProvider('gemini','secret','m'),'q','app','',None,None)
    next(g);g.close()
    p=_isolated_usage.snapshot()['gemini']
    assert p['requests']==1 and p['unknown_usage']==1
