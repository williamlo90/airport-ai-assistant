"""Signed local demo client. Never follow redirects or use environment proxies."""

import argparse
import json
from pathlib import Path
from uuid import UUID
import httpx
from airport_reference.config import load_settings
from airport_reference.security import signed_headers

TENANTS={'airport-a':UUID('11111111-1111-4111-8111-111111111111'),'airport-b':UUID('22222222-2222-4222-8222-222222222222')}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('resource',choices=('booking','booking-transactions','transaction','policy','relations'))
    parser.add_argument('reference',nargs='?')
    parser.add_argument('--subject',choices=('owner','representative','beneficiary','payer','outsider'),default='owner')
    parser.add_argument('--tenant',choices=tuple(TENANTS),default='airport-a')
    parser.add_argument('--env-file',type=Path,default=Path('reference_service/.env'))
    args=parser.parse_args()
    if args.resource!='relations' and not args.reference:parser.error('A reference is required.')
    if args.reference and (len(args.reference)>80 or not all(ch.isascii() and (ch.isalnum() or ch in '_-') for ch in args.reference)):parser.error('Invalid reference format.')
    try:
        settings=load_settings(args.env_file)
        paths={'booking':'/source/v1/bookings/','booking-transactions':'/source/v1/bookings/','transaction':'/source/v1/transactions/','policy':'/source/v1/policies/'}
        path='/source/v1/subjects/me/relations' if args.resource=='relations' else paths[args.resource]+args.reference+('/transactions' if args.resource=='booking-transactions' else '')
        headers=signed_headers(settings.service_token.get_secret_value(),settings.context_secret.get_secret_value(),TENANTS[args.tenant],args.subject,path=path)
        # Destination cannot be selected by request input or untrusted configuration.
        with httpx.Client(trust_env=False,follow_redirects=False,timeout=5) as client:
            result=client.get(f'http://127.0.0.1:{settings.port}'+path,headers=headers)
        if result.is_redirect:raise ValueError('Unexpected redirect.')
        print('HTTP',result.status_code)
        print(json.dumps(result.json(),indent=2,ensure_ascii=False))
        if result.is_error:raise SystemExit(1)
    except Exception:
        print('Demo request failed; credentials and connection details redacted. Ensure the local reference API is running.')
        raise SystemExit(1) from None


if __name__=='__main__':main()
