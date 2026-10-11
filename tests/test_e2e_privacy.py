import importlib.util
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('e2e_checks',ROOT/'tests/e2e/win_e2e.py')
e2e=importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)


def test_mask_removes_background_preserves_coordinate_frame():
    img=Image.new('RGB',(200,100),'red')
    mon={'left':100,'top':20}
    safe=e2e.mask_to_rect(img,mon,(150,40,250,100))
    assert safe.size==img.size
    assert safe.getpixel((0,0))==(255,255,255)
    assert safe.getpixel((50,20))==(255,0,0)
    assert safe.getpixel((151,81))==(255,255,255)


def test_missing_synthetic_window_refuses_capture(monkeypatch):
    monkeypatch.setattr(e2e,'SYNTHETIC_RECT',None)
    with pytest.raises(RuntimeError,match='synthetic'):
        e2e.safe_image({'left':0,'top':0},Image.new('RGB',(100,100)))


def test_workflow_minimal_permissions_and_public_summary_only():
    text=(ROOT/'.github/workflows/e2e-windows.yml').read_text()
    assert 'permissions:\n  contents: read' in text
    assert 'verified_free_key' in text
    assert 'cp -r "$d"' not in text
    assert "path: e2e-safe" in text
    assert "'detail'" not in text
    assert 'contents: write' in text  # publisher only


def test_display_setup_is_ci_only():
    text=(ROOT/'tests/e2e/set_display.py').read_text()
    assert 'GITHUB_ACTIONS' in text and 'refusing to alter a local PC' in text


def test_public_summary_excludes_arbitrary_content():
    spec=importlib.util.spec_from_file_location('summary',ROOT/'tests/e2e/public_summary.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    d={'sha':'abc','config':'native','passed':1,'failed':0,'skipped':1,
       'checks':[{'check':'real_model_teach','status':'skip','detail':'SECRET PRIVATE TEXT',
                  'rows':[{'response':'PRIVATE'}]}], 'secret':'KEY'}
    safe=module.summarize(d)
    assert 'PRIVATE' not in str(safe) and 'SECRET' not in str(safe) and 'KEY' not in str(safe)
    assert safe['capability']=='inconclusive'
