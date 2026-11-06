"""Translation-only scope; reuse the bounded physical-attempt executor."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import agent_selection as e
import agent_acceptance_v3 as bounded

ROOT=e.ROOT
DATA=ROOT/'evaluations/datasets/translation_acceptance_v1.json'
CONFIG=ROOT/'evaluations/rubrics/translation_acceptance_v1.json'
OUT=ROOT/'local/ai-04-translation-v1'
_BASE_HASHES=e.source_hashes


def source_hashes():
    hashes=_BASE_HASHES()
    paths=[Path(__file__),Path(bounded.__file__),*Path(__file__).parent.glob('test_translation_acceptance*.py'),
           *Path(__file__).parent.glob('test_agent_acceptance*.py'),*Path(__file__).parent.glob('test_agent_request*.py')]
    for p in paths:hashes[p.relative_to(ROOT).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    return hashes


def configure():
    e.DATA,e.CONFIG,e.OUT=DATA,CONFIG,OUT
    e.source_hashes=source_hashes
    return e


async def live(approved_hash,approved_cap,**kwargs):
    return await bounded.live(approved_hash,approved_cap,_configure=configure,**kwargs)


def assess(folder,review_file):
    return bounded.assess(folder,review_file,_configure=configure)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['prepare','live','assess'])
    parser.add_argument('--approved-manifest');parser.add_argument('--approved-cap-usd');parser.add_argument('--run-folder',type=Path);parser.add_argument('--review-file',type=Path)
    args=parser.parse_args();configure()
    try:
        if args.command=='prepare':
            manifest=asyncio.run(e.prepare());print(json.dumps({k:manifest[k] for k in ('manifest_hash','max_http_attempts','reservation_usd','cap_usd')}))
        elif args.command=='live':print(json.dumps(asyncio.run(live(args.approved_manifest,args.approved_cap_usd))))
        else:print(json.dumps(assess(args.run_folder,args.review_file)))
    except (e.SafeFailure,ValueError,OSError,KeyError,TypeError,StopIteration):
        print(json.dumps({'failed':'translation_scope_stopped_check_sanitized_artifacts'}));raise SystemExit(1) from None
