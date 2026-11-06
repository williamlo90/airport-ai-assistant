"""Secret-safe prerequisites only; never sends mail or claims provider delivery."""
import argparse,asyncio
from pathlib import Path
from airport_assistant.mail_config import load_mail_settings
from airport_assistant.config import ConfigurationError
from airport_assistant.composition import database_loop_factory


def readiness(settings):
    missing=[]
    for flag in ('auth_enabled','enabled','ingress_enabled','receiving_enabled','reply_enabled'):
        if not getattr(settings,flag):missing.append('APP_MAIL_'+flag.upper())
    if not settings.accounts:missing.append('APP_MAIL_ACCOUNTS')
    if not settings.auth_routes:missing.append('APP_MAIL_AUTH_ROUTES')
    if not settings.webhooks:missing.append('APP_MAIL_WEBHOOKS')
    return missing


def main():
    parser=argparse.ArgumentParser(description='Check MAIL-05 prerequisites without sending email.')
    parser.add_argument('--env-file',type=Path,default=Path('.env'))
    parser.add_argument('--database',action='store_true',help='Check current account metadata using restricted runtime credential.')
    args=parser.parse_args()
    try:
        settings=load_mail_settings(args.env_file)
        from airport_assistant.config import load_settings
        app=load_settings(args.env_file)
        missing=readiness(settings)
        if not app.database_enabled:missing.append('APP_DATABASE_ENABLED')
        if not app.auth_enabled:missing.append('APP_AUTH_ENABLED')
        if app.auth_enabled:
            from airport_assistant.auth_config import load_auth_settings
            load_auth_settings(args.env_file)
        if settings.auth_enabled and not set(route.account_ref for route in settings.auth_routes.values())<=set(item.account_ref for item in settings.webhooks.values()):
            missing.append('APP_MAIL_WEBHOOKS_ACCOUNT_COVERAGE')
        if missing:
            print('NOT READY: '+', '.join(missing));raise SystemExit(2)
        if args.database:
            from airport_assistant.database_config import RuntimeDatabaseSettings,load_database_settings
            from airport_assistant.infrastructure.database.connections import RuntimeDatabase
            from airport_assistant.infrastructure.database.verification_mail import PostgresVerificationRouting
            async def check():
                config=load_database_settings(RuntimeDatabaseSettings,args.env_file)
                db=RuntimeDatabase(config.url.get_secret_value());db.auth_mail_required=True
                try:
                    await db.open();routing=PostgresVerificationRouting(db)
                    return all([await routing.allowed(tenant,route) for tenant,route in settings.auth_routes.items()])
                finally:await db.close()
            with asyncio.Runner(loop_factory=database_loop_factory) as runner:valid=runner.run(check())
            if not valid:print('NOT READY: inactive or mismatched tenant/account route.');raise SystemExit(2)
        print('Configuration prerequisites ready. Provider/domain/mailbox/webhook/round-trip verification remains pending.')
    except ConfigurationError as error:
        print(str(error));raise SystemExit(2) from None
    except Exception:
        print('Mail prerequisite check failed; details redacted.');raise SystemExit(2) from None


if __name__=='__main__':main()
