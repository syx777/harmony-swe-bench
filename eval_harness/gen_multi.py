import os, sys, traceback
from harmony_bench.graph import build_repo_index, build_symbol_graph
from harmony_bench.carve import carve
from harmony_bench.carve.closure import CarveError
from harmony_bench.hollow import hollow_l2
from harmony_bench.author import author_issue, author_test, module_root
from harmony_bench.verify import gate
from harmony_bench.build import build_instance
from harmony_bench.schema import write_instance
from harmony_bench.cli import _head_commit
from harmony_bench.llm.client import load_config, build_client

REPO=os.path.abspath('..'); TC=os.path.abspath('../../harmony_toolchain'); OUT='data/instances'
client=build_client(load_config('config/default.yaml'))
idx=build_repo_index(REPO,['common','features','products']); sg=build_symbol_graph(idx)
commit=_head_commit(REPO)

# discover all fan_in=0 multi-file candidates (carve-OK, >=2 files)
cands=[]
for f,pf in idx.files.items():
    if '/test/' in f or '/ohosTest/' in f: continue
    exp={e.name for e in pf.exports}|({pf.default_export_name} if pf.default_export_name else set())
    for s in list(pf.structs)+list(pf.functions):
        if s.name in exp and sg.fan_in_in(s.name,f)==0:
            try:
                cv=carve(idx,sg,s.name,f)
            except CarveError: continue
            if len(cv.extract_files)>=2:
                cands.append((s.name,f,cv))
print('multi-file candidates discovered: %d'%len(cands),flush=True)
for n,f,cv in cands: print('  -',n,'files=%d'%len(cv.extract_files),[x.split('/')[-1] for x in cv.extract_files],flush=True)
print('=== running sequentially ===',flush=True)

ok=0
for (name,f,cv) in cands:
    try:
        hr=hollow_l2(idx,cv)
        issue=author_issue(client,cv)          # sequential LLM call 1
        f2p=author_test(client,cv,REPO)        # sequential LLM call 2
        inst=build_instance(cv,hr,issue,f2p,REPO,commit)
        mroot=module_root(cv.anchor_file,REPO)
        files=[(fh.target_rel,fh.broken_source,fh.golden_source) for fh in hr.files]
        gr=gate(REPO,TC,mroot,files,f2p)
        inst.validation.integrity_green=gr.integrity_green
        inst.validation.broken_red=gr.broken_red
        inst.validation.gold_green=gr.gold_green
        if gr.passed:
            p=write_instance(inst,OUT); ok+=1
            print('[PASS] %-26s -> %s (%d files, %d syms)'%(name,os.path.basename(p),len(cv.extract_files),len(hr.deleted_names)),flush=True)
        else:
            print('[DROP] %-26s integrity=%s broken=%s gold=%s'%(name,gr.integrity_verdict,gr.broken_verdict,gr.gold_verdict),flush=True)
    except Exception as e:
        print('[ERR ] %-26s %s'%(name,str(e)[:120]),flush=True)
print('=== done: %d/%d passed & written ==='%(ok,len(cands)),flush=True)
