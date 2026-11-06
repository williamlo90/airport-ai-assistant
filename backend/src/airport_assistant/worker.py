"""Executable worker; provider handlers are supplied by their feature modules."""
import argparse
import asyncio
from pathlib import Path
from airport_assistant.composition import database_loop_factory,load_worker_configuration,build_worker_resources,build_job_handlers
from airport_assistant.modules.jobs.service import JobWorker


def build_handlers(database=None,path=None,*,scheduler=None):
    # MAIL-01 owns business/provider checks. Never use dummy success here.
    return build_job_handlers(database,path,scheduler=scheduler)


async def run(database,settings,handlers,*,scheduler,once=False):
    try:
        await database.open()
        worker=JobWorker(scheduler,handlers,lease_seconds=settings.lease_seconds,handler_timeout=settings.handler_timeout)
        if not handlers:
            if once:
                print('PASS: worker credential verified; no handlers registered, queued jobs untouched.')
                return
            raise ValueError('Worker needs registered handlers.')
        while True:
            result=await worker.run_once()
            print('worker_iteration state='+result)
            if once:return
            if result=='idle':await asyncio.sleep(settings.poll_seconds)
    finally:await database.close()


def main():
    parser=argparse.ArgumentParser(description='Run lease-scoped outbox handlers with fresh business authorization.')
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--env-file',type=Path,default=Path('.env'))
    group.add_argument('--no-env-file',action='store_true')
    parser.add_argument('--check-config',action='store_true');parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    try:
        path=None if args.no_env_file else args.env_file
        settings,db_settings=load_worker_configuration(path)
        if args.check_config:
            from airport_assistant.composition import check_worker_mail_configuration
            check_worker_mail_configuration(path)
            print('Worker configuration valid.');return
        database,scheduler=build_worker_resources(db_settings)
        with asyncio.Runner(loop_factory=database_loop_factory) as runner:runner.run(run(database,settings,build_handlers(database,path,scheduler=scheduler),scheduler=scheduler,once=args.once))
    except KeyboardInterrupt:print('Worker stopped; unfinished leases recover by replay policy.')
    except Exception:
        print('Worker failed; details redacted. Check configuration, privileges and registered handlers.')
        raise SystemExit(1) from None


if __name__=='__main__':main()
