"""Actual isolated Neon migration using the installed candidate wheel, never app schema."""
import json,os,sys,traceback
from contextlib import contextmanager
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend/tests/integration'))

def main():
 import airport_assistant
 from airport_assistant.infrastructure.database.migrations import migrate,migration_directory
 assert '/src/' not in Path(airport_assistant.__file__).as_posix()
 directory=migration_directory();assert directory.name=='_migrations'
 from alembic.script import ScriptDirectory
 from airport_assistant.infrastructure.database.migrations import migration_config
 assert ScriptDirectory.from_config(migration_config()).get_heads()==['0038_staff_administration']
 from test_private_source_schema_neon import target as base
 from test_private_source_graph_neon import target as graph
 out=Path(os.environ['RELEASE_PROOF_DIR']);out.mkdir(parents=True,exist_ok=True)
 result={'passed':False,'schema':None,'installed_migration_directory':str(directory),'packaged_sql':len(list((directory/'sql').glob('*.sql')))}
 try:
  with contextmanager(base.__wrapped__)() as t:
   with contextmanager(graph.__wrapped__)(t) as t:
    result['schema']=t.schema
    result['head']=migrate(t.direct,schema=t.schema,runtime_role=t.roles[0],policy_role=t.roles[1],auth_role=t.roles[2],target_revision='0038_staff_administration')
    assert result['head']=='0038_staff_administration'
    assert migrate(t.direct,schema=t.schema,action='current')==result['head']
    result['passed']=True
 except Exception as e:
  result.update(error_type=type(e).__name__,frames=[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno} for f in traceback.extract_tb(e.__traceback__)])
 finally:
  result['clean_git_after']=not __import__('subprocess').check_output(['git','status','--porcelain'],cwd=ROOT)
  (out/'migration-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
 return 0 if result['passed'] and result['clean_git_after'] else 1
if __name__=='__main__':raise SystemExit(main())
