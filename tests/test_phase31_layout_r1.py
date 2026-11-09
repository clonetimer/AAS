"""Phase31 R1 visual-regression contract: finder is an on-demand dialog.

This static contract guards the layout at rest; a real browser smoke is still
required on an environment permitting Chromium navigation.
"""
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1] / 'web' / 'visualization'

class DomOutline(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack=[]
        self.seen=[]
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        self.seen.append((tag,a,tuple(self.stack)))
        if tag not in ('input','link','meta','br','img','hr','source','wbr'):
            self.stack.append((tag,a.get('id'),a.get('class','')))
    def handle_endtag(self, tag):
        if not self.stack:return
        if self.stack[-1][0]==tag:self.stack.pop()
        else:
            for i in range(len(self.stack)-1,-1,-1):
                if self.stack[i][0]==tag:
                    del self.stack[i:];return

def test_fault_finder_is_modal_and_default_grid_unchanged():
    markup=(ROOT/'browser.html').read_text('utf-8')
    dom=DomOutline();dom.feed(markup)
    v2_left=markup.split('<div class="v2-left">',1)[1].split('<div class="v2-right">',1)[0]
    assert '<h2>筛选条件</h2>' in v2_left.split('</section>',1)[0]
    assert 'v2-fault-finder' not in v2_left
    assert 'id="v2OpenFaultFinder"' in v2_left
    assert 'id="v2OpenImport"' in v2_left
    assert 'id="v2Candidates"' in v2_left
    matches=[entry for entry in dom.seen if entry[1].get('id')=='v2FaultDialog']
    assert len(matches)==1 and matches[0][0]=='dialog'
    assert matches[0][2][-1][0]=='main'  # not inside the two-column grid
    children=[entry for entry in dom.seen if entry[1].get('id') in ('v2FindFaults','v2FaultType','v2FaultResults','v2FaultStatus')]
    assert len(children)==4 and all(any(item[1]=='v2FaultDialog' for item in entry[2]) for entry in children)
    assert 'id="v2CloseFaultFinder"' in markup


def test_dialog_interactions_and_original_css_grid_contract():
    js=(ROOT/'telemetry-v2-browser.js').read_text('utf-8')
    css=(ROOT/'telemetry-v2-browser.css').read_text('utf-8')
    assert 'faultDialog.showModal()' in js
    assert "byId('v2CloseFaultFinder').addEventListener('click',()=>faultDialog.close())" in js
    assert "if(byId('v2FaultDialog').open)byId('v2FaultDialog').close()" in js
    assert '.v2-layout {display:grid;grid-template-columns:minmax(290px,36%) minmax(0,1fr)' in css
    assert '.v2-fault-dialog:not' not in css
    assert '.v2-fault-dialog[open]{display:flex;flex-direction:column}' in css
    assert 'id="v2Chart"' in (ROOT/'browser.html').read_text('utf-8')
    if node:=shutil.which('node'):
        p=subprocess.run([node,'--check',str(ROOT/'telemetry-v2-browser.js')],capture_output=True,text=True)
        assert p.returncode==0,p.stderr
