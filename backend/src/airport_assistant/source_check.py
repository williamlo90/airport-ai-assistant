"""Operator-only fictional loopback connector diagnostic; not customer login."""

import argparse
import asyncio
from pathlib import Path
from uuid import UUID
from airport_assistant.config import load_settings
from airport_assistant.source_config import load_source_settings
from airport_assistant.integrations.source.client import SourceHttpClient
from airport_assistant.modules.source.models import SourceCaller,SourceFailure


def main():
    parser=argparse.ArgumentParser(description='Exercise the backend adapter against local fictional fixtures only.')
    parser.add_argument('resource',choices=('booking','transaction','booking-transactions','policy','relations'))
    parser.add_argument('reference',nargs='?')
    parser.add_argument('--subject',choices=('owner','representative','beneficiary','payer','outsider'),default='owner')
    parser.add_argument('--airport',choices=('a','b'),default='a')
    parser.add_argument('--env-file',type=Path,default=Path('.env'))
    args=parser.parse_args()
    if args.resource!='relations' and args.reference is None:parser.error('Reference required.')
    async def check(settings):
        client=SourceHttpClient(settings.base_url,settings.token.get_secret_value(),settings.context_secret.get_secret_value(),
                                timeout=settings.timeout,max_response_bytes=settings.max_response_bytes)
        tenant=UUID('11111111-1111-4111-8111-111111111111' if args.airport=='a' else '22222222-2222-4222-8222-222222222222')
        try:
            caller=SourceCaller(tenant,args.subject)
            if args.resource=='relations':return await client.relations(caller)
            method={'booking':client.booking,'transaction':client.transaction,'booking-transactions':client.booking_transactions,'policy':client.policy}[args.resource]
            return await method(caller,args.reference)
        finally:await client.aclose()
    try:
        app=load_settings(args.env_file);settings=load_source_settings(args.env_file)
        if app.env=='production' or not settings.allow_loopback_http or not settings.base_url.startswith(('http://127.0.0.1:','http://[::1]:')):raise ValueError('Only local fictional reference diagnostics permitted.')
        result=asyncio.run(check(settings))
    except SourceFailure as exc:
        print('HTTP',exc.status,str(exc));raise SystemExit(1) from None
    except Exception:
        print('Source diagnostic failed; configuration and connection details redacted.')
        raise SystemExit(1) from None
    print('HTTP 200 (backend adapter; fictional operator diagnostic)')
    print(result.model_dump_json(indent=2,exclude_none=True))


if __name__=='__main__':main()
