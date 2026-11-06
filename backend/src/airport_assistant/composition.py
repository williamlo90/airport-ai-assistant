"""Composition root: assemble services once per application, never globally.

Concrete repository/provider bindings belong here as features become executable.
Do not put transactions or tenant/actor session state in this application scope.
"""

from airport_assistant.modules.knowledge.service import KnowledgeService
from airport_assistant.infrastructure.database.knowledge import PostgresKnowledge

from dataclasses import dataclass
from contextlib import asynccontextmanager
import asyncio
import sys

from airport_assistant.infrastructure.database.migrations import validate_schema
from airport_assistant.database_config import RuntimeDatabaseSettings
from airport_assistant.infrastructure.database.connections import RuntimeDatabase
from airport_assistant.modules.system.health import HealthService, ProcessHealthService
from airport_assistant.auth_config import AuthSettings
from airport_assistant.infrastructure.database.auth_repository import PostgresAuthRepository
from airport_assistant.modules.identity.auth import AuthService,AuthPolicy,VerificationSender
from airport_assistant.modules.identity.staff import StaffService
from airport_assistant.modules.identity.access import AccessService
from airport_assistant.source_config import SourceSettings
from airport_assistant.modules.source.service import SourceReadService
from airport_assistant.modules.source.grants import CurrentSourceGrants
from airport_assistant.modules.source.labels import PrivateSourceAccess
from airport_assistant.modules.conversations.service import ConversationService
from airport_assistant.infrastructure.database.conversation_repository import PostgresConversations
from airport_assistant.modules.drafts.service import DraftService
from airport_assistant.infrastructure.database.draft_repository import PostgresDrafts
from airport_assistant.infrastructure.database.case_repository import PostgresCases
from airport_assistant.modules.cases.service import CaseService
from airport_assistant.modules.cases.lifecycle import CaseLifecycle
from airport_assistant.modules.cases.capabilities import StaffCapabilities,AssigneeCursor
from airport_assistant.modules.cases.queue import StaffQueue,QueueCursor
from airport_assistant.modules.cases.customer import CustomerCases,CustomerCaseCursor
from airport_assistant.modules.cases.information import CustomerInformation
from airport_assistant.modules.cases.information_preview import InformationPreviewCodec
from airport_assistant.infrastructure.database.customer_information import PostgresCustomerInformation
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle
from airport_assistant.integrations.source.client import SourceHttpClient
from airport_assistant.infrastructure.database.source_subjects import PostgresSourceSubjects
from airport_assistant.infrastructure.database.staff_repository import PostgresStaffRepository
from airport_assistant.infrastructure.security.passwords import BoundedPasswordVerifier
from airport_assistant.modules.attachments.service import AttachmentService
from airport_assistant.modules.conversations.handoff import ChatHandoff
from airport_assistant.modules.conversations.publication import StaffMessages,AIPublication
from airport_assistant.infrastructure.database.publication import PostgresPublication
from airport_assistant.modules.conversations.events import ConversationEvents,EventCursor
from airport_assistant.infrastructure.database.conversation_events import PostgresConversationEvents
from airport_assistant.infrastructure.database.attachment_repository import PostgresAttachments
from airport_assistant.infrastructure.security.file_scanner import ClamAVScanner
from airport_assistant.infrastructure.security.amsi_scanner import AmsiScanner
from airport_assistant.modules.cases.ratings import CustomerRatings
from airport_assistant.infrastructure.database.ratings import PostgresRatings
from airport_assistant.modules.identity.administration import StaffAdministration
from airport_assistant.infrastructure.database.administration import PostgresAdministration


@dataclass(frozen=True)
class ApplicationServices:
    health: HealthService
    auth: AuthService|None=None
    staff: StaffService|None=None
    access: AccessService|None=None
    source: SourceReadService|None=None
    source_grants: CurrentSourceGrants|None=None
    private_source_access: PrivateSourceAccess|None=None
    conversations: ConversationService|None=None
    drafts: DraftService|None=None
    cases: CaseService|None=None
    case_lifecycle: CaseLifecycle|None=None
    staff_capabilities: StaffCapabilities|None=None
    staff_queue: StaffQueue|None=None
    customer_cases: CustomerCases|None=None
    customer_information: CustomerInformation|None=None
    ratings: CustomerRatings|None=None
    administration: StaffAdministration|None=None
    attachments: AttachmentService|None=None
    conversation_events: ConversationEvents|None=None
    chat_handoff: ChatHandoff|None=None
    staff_messages: StaffMessages|None=None
    ai_publication: AIPublication|None=None
    delivery_status: object|None=None
    email_ingress: object|None=None
    knowledge: KnowledgeService|None=None
    agent: object|None=None
    agent_workflow: object|None=None
    agent_delivery: object|None=None
    staff_translations: object|None=None


def build_services(database:RuntimeDatabase|None=None,auth_settings:AuthSettings|None=None,sender:VerificationSender|None=None,source_client=None,tenant_map=None,scanner_port=None,mail_settings=None,knowledge_settings=None,agent_settings=None,*,private_enabled=False,schema='app',scanner_backend='clamav') -> ApplicationServices:
    validate_schema(schema)
    if database is not None and getattr(database,'schema',schema)!=schema:raise ValueError('Database and repository schemas must match.')
    if type(private_enabled) is not bool:raise ValueError('Explicit private source switch required.')
    if private_enabled and (database is None or auth_settings is None or source_client is None):
        raise ValueError('Private source workflows require authenticated database and source services.')
    if private_enabled:database.require_private_mail_history(schema)
    auth=None
    staff=None
    access=None
    if sender is None and mail_settings is not None and mail_settings.auth_enabled:
        sender=build_verification_sender(mail_settings,database=database,schema=schema)
    if auth_settings is not None:
        if database is None:raise ValueError("Authentication requires a database resource.")
        auth=AuthService(PostgresAuthRepository(database,schema),auth_settings.session_secret.get_secret_value().encode(),
                         AuthPolicy(**auth_settings.model_dump(exclude={"session_secret"})),sender)
    if auth is not None:
        repository=PostgresStaffRepository(database,schema)
        access=AccessService(auth,repository)
        staff=StaffService(auth,repository,BoundedPasswordVerifier(),access)
    source=SourceReadService(source_client,PostgresSourceSubjects(database,schema),tenant_map) if source_client is not None and database is not None and access is not None else None
    source_grants=CurrentSourceGrants(source,source_client) if source is not None else None
    private_source_access=PrivateSourceAccess(source_grants) if source_grants is not None else None
    conversations=ConversationService(PostgresConversations(database,schema),private_source_access) if access is not None else None
    drafts=DraftService(PostgresDrafts(database,schema),private_source_access) if access is not None else None
    cases=CaseService(PostgresCases(database,schema),private_source_access) if access is not None else None
    case_lifecycle=CaseLifecycle(PostgresCaseLifecycle(database,schema),private_source_access,private_mail_history=private_enabled) if access is not None else None
    customer_information=CustomerInformation(PostgresCustomerInformation(database,schema),InformationPreviewCodec(auth_settings.session_secret.get_secret_value().encode()),private_source_access) if access is not None else None
    ratings=CustomerRatings(PostgresRatings(database,schema),private_source_access) if access is not None else None
    administration=StaffAdministration(PostgresAdministration(database,schema)) if access is not None else None
    if scanner_backend not in {'clamav','amsi-local'}:raise ValueError('Unknown scanner backend.')
    if scanner_backend=='amsi-local' and scanner_port is not None:raise ValueError('Conflicting scanner backends.')
    scanner=AmsiScanner() if scanner_backend=='amsi-local' else ClamAVScanner(scanner_port) if scanner_port is not None else None
    attachments=AttachmentService(PostgresAttachments(database,schema),scanner,private_source_access) if access is not None else None
    from airport_assistant.modules.mail.delivery import DeliveryStatus
    from airport_assistant.infrastructure.database.delivery_status import PostgresDeliveryStatus
    from airport_assistant.modules.mail.inbox import WebhookIngress
    from airport_assistant.infrastructure.database.inbox import PostgresInbox
    if database is not None and mail_settings is not None and mail_settings.ingress_enabled:database.webhook_required=True
    ingress=WebhookIngress(PostgresInbox(database,schema),mail_settings.webhooks) if database is not None and mail_settings is not None and mail_settings.ingress_enabled else None
    knowledge=KnowledgeService(PostgresKnowledge(database,schema)) if access is not None else None
    if knowledge_settings is not None and knowledge_settings.enabled:
        if knowledge is None:raise ValueError('Knowledge requires authenticated database services.')
        from airport_assistant.modules.knowledge.retrieval import KnowledgeRetrieval
        from airport_assistant.integrations.embeddings.openai import OpenAIEmbeddings
        knowledge.retrieval=KnowledgeRetrieval(knowledge,OpenAIEmbeddings(knowledge_settings.api_key.get_secret_value()))
    agent=None
    agent_workflow=None
    agent_delivery=None
    staff_translations=None
    publication_repository=PostgresPublication(database,schema) if access is not None else None
    private_publication=None
    if private_enabled:
        from airport_assistant.modules.conversations.ai_publication import PrivateAIPublication
        private_publication=PrivateAIPublication(publication_repository,private_source_access)
    ai_publication=AIPublication(publication_repository,private_capability=private_publication) if access is not None else None
    if agent_settings is not None and agent_settings.enabled:
        if conversations is None or drafts is None:raise ValueError('Agent requires authenticated conversation and draft services.')
        from airport_assistant.modules.agent.service import AgentFoundation
        from airport_assistant.integrations.agent.openai import OpenAIPlanner
        evidence=None
        if agent_settings.knowledge_min_score is not None:
            if knowledge is None or knowledge.retrieval is None:raise ValueError('Agent knowledge policy requires configured retrieval.')
            from airport_assistant.modules.knowledge.evidence import KnowledgeEvidence,EvidencePolicy
            evidence=KnowledgeEvidence(knowledge.retrieval,EvidencePolicy(agent_settings.knowledge_min_score,agent_settings.knowledge_policy_id),auth_settings.session_secret.get_secret_value().encode())
        agent=AgentFoundation(OpenAIPlanner(agent_settings.api_key.get_secret_value(),agent_settings.model),conversations,drafts,source,private_source_access,evidence)
        from airport_assistant.modules.agent.workflow import AgentWorkflow
        from airport_assistant.infrastructure.database.agent_runs import PostgresAgentRuns
        from airport_assistant.integrations.agent.graph import AgentGraph
        agent_workflow=AgentGraph(AgentWorkflow(agent,PostgresAgentRuns(database,schema),private_persistence=private_enabled))
        from airport_assistant.modules.agent.delivery import AgentDelivery
        from airport_assistant.modules.agent.translation import StaffTranslations
        from airport_assistant.infrastructure.database.translations import PostgresTranslations
        from airport_assistant.modules.agent.answers import PublicAnswers
        answers=PublicAnswers(evidence) if evidence else None
        if private_publication is not None:private_publication.answers=answers
        ai_publication=AIPublication(publication_repository,answers,private_capability=private_publication)
        agent_delivery=AgentDelivery(agent_workflow,ai_publication,auth_settings.session_secret.get_secret_value().encode())
        translation_repository=PostgresTranslations(database,schema,mail_enabled=True)
        staff_translations=StaffTranslations(translation_repository,StaffMessages(translation_repository,private_source_access,private_disclosure=private_enabled),agent.model,private_translation=private_enabled)
    return ApplicationServices(administration=administration,ratings=ratings,customer_information=customer_information,agent_delivery=agent_delivery,staff_translations=staff_translations,agent_workflow=agent_workflow,agent=agent,knowledge=knowledge,staff_queue=StaffQueue(PostgresCaseLifecycle(database,schema),QueueCursor(auth_settings.session_secret.get_secret_value().encode())) if access is not None else None,staff_capabilities=StaffCapabilities(PostgresCaseLifecycle(database,schema),AssigneeCursor(auth_settings.session_secret.get_secret_value().encode())) if access is not None else None,email_ingress=ingress,delivery_status=DeliveryStatus(PostgresDeliveryStatus(database,schema),private_history=private_enabled) if access is not None else None,health=ProcessHealthService(),auth=auth,staff=staff,access=access,source=source,
                               source_grants=source_grants,private_source_access=private_source_access,conversations=conversations,drafts=drafts,cases=cases,case_lifecycle=case_lifecycle,customer_cases=CustomerCases(PostgresCaseLifecycle(database,schema),CustomerCaseCursor(auth_settings.session_secret.get_secret_value().encode()),private_source_access) if access is not None else None,attachments=attachments,staff_messages=StaffMessages(PostgresPublication(database,schema,mail_enabled=True),private_source_access,private_disclosure=private_enabled) if access is not None else None,ai_publication=ai_publication,chat_handoff=ChatHandoff(PostgresCaseLifecycle(database,schema)) if access is not None else None,conversation_events=ConversationEvents(PostgresConversationEvents(database,schema),EventCursor(auth_settings.session_secret.get_secret_value().encode()),private_source_access) if access is not None else None)


@dataclass(frozen=True)
class ApplicationResources:
    database: RuntimeDatabase | None = None
    source: SourceHttpClient | None = None

    @asynccontextmanager
    async def lifecycle(self):
        try:
            if self.database is not None:await self.database.open()
            yield
        finally:
            try:
                if self.source is not None:await self.source.aclose()
            finally:
                if self.database is not None:await self.database.close()


def build_resources(settings: RuntimeDatabaseSettings | None,source_settings:SourceSettings|None=None,*,schema='app') -> ApplicationResources:
    database=RuntimeDatabase(
        settings.url.get_secret_value(),pool_max=settings.pool_max,max_waiting=settings.max_waiting,
        connect_timeout=settings.connect_timeout,acquire_timeout=settings.acquire_timeout,
        statement_timeout_ms=settings.statement_timeout_ms,schema=schema) if settings else None
    source=SourceHttpClient(source_settings.base_url,source_settings.token.get_secret_value(),source_settings.context_secret.get_secret_value(),
                            timeout=source_settings.timeout,max_response_bytes=source_settings.max_response_bytes) if source_settings else None
    return ApplicationResources(database,source)


def database_loop_factory():
    # Psycopg async connections require a selector loop on Windows.
    return asyncio.SelectorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()


# Worker has a separate credential/pool and no API/session composition.
def load_worker_configuration(path):
    from airport_assistant.worker_config import WorkerDatabaseSettings,load_worker_settings
    from airport_assistant.database_config import load_database_settings
    return load_worker_settings(path),load_database_settings(WorkerDatabaseSettings,path,category='worker database')


def build_worker_resources(settings):
    from airport_assistant.infrastructure.database.jobs import WorkerDatabase,PostgresScheduler
    db=WorkerDatabase(settings.url.get_secret_value(),pool_max=settings.pool_max,max_waiting=settings.max_waiting,connect_timeout=settings.connect_timeout,acquire_timeout=settings.acquire_timeout,statement_timeout_ms=settings.statement_timeout_ms)
    return db,PostgresScheduler(db)


def build_job_handlers(database=None,path=None,*,mail_settings=None,transport=None,private_access=None,scheduler=None):
    from airport_assistant.mail_config import load_mail_settings
    from airport_assistant.infrastructure.database.mail import PostgresMail
    from airport_assistant.modules.mail.service import OutboundMail
    from airport_assistant.modules.mail.delivery import ReconcileMail
    from airport_assistant.modules.mail.inbox import InboxWorker
    from airport_assistant.modules.mail.replies import ReplyWorker,ChallengeWorker,ReplyTransport
    from airport_assistant.infrastructure.database.replies import PostgresReplies
    from airport_assistant.infrastructure.database.inbox import PostgresInbox
    from airport_assistant.integrations.mail.resend import ResendTransport
    settings=mail_settings or load_mail_settings(path)
    if scheduler is not None:scheduler.mail_accounts=tuple(settings.accounts) if settings.enabled or settings.receiving_enabled else ()
    if not settings.enabled and not settings.receiving_enabled:return {}
    if database is None:raise ValueError('Mail requires a worker database.')
    # Enabled MAIL-02 must fail startup on a pre-delivery schema, before claim.
    database.mail_delivery_required=True
    sender=transport or ResendTransport({ref:key.get_secret_value() for ref,key in settings.accounts.items()})
    secret=resolve_reply_secret(settings,path) if settings.reply_enabled else None
    if secret is not None:sender=ReplyTransport(sender,secret)
    handler=OutboundMail(PostgresMail(database),sender,account_ref=settings.accounts.keys(),private_access=private_access,reply_secret=secret)
    handlers={'case_created':handler,'email_send':handler,'email_reconcile':ReconcileMail(PostgresMail(database),sender,account_refs=settings.accounts)} if settings.enabled else {}
    if settings.receiving_enabled:
        database.mail_inbox_required=True
        inbox=InboxWorker(PostgresInbox(database),PostgresMail(database),sender,accounts=settings.accounts)
        handlers.update(email_fetch=inbox,email_event=inbox)
    if settings.reply_enabled:
        database.mail_reply_required=True
        handlers.update(email_reply=ReplyWorker(PostgresReplies(database),secret),email_verify_send=ChallengeWorker(PostgresReplies(database),sender,secret))
    return handlers


def check_worker_mail_configuration(path):
    from airport_assistant.mail_config import load_mail_settings
    settings=load_mail_settings(path)
    if settings.reply_enabled:resolve_reply_secret(settings,path)


def resolve_reply_secret(settings,path):
    if settings.reply_secret is not None:return settings.reply_secret.get_secret_value().encode()
    from airport_assistant.auth_config import load_auth_settings
    import hmac,hashlib
    return hmac.new(load_auth_settings(path).session_secret.get_secret_value().encode(),b'airport-mail-replies-v1',hashlib.sha256).digest()


def build_verification_sender(settings,*,database=None,transport=None,routing=None,schema='app'):
    if not settings.auth_enabled:return None
    from airport_assistant.integrations.mail.resend import ResendTransport
    from airport_assistant.modules.mail.verification import VerificationMail
    from airport_assistant.infrastructure.database.verification_mail import PostgresVerificationRouting
    if routing is None:
        if database is None:raise ValueError('Authentication mail requires database routing.')
        database.auth_mail_required=True
        routing=PostgresVerificationRouting(database,schema)
    return VerificationMail(settings.auth_routes,transport or ResendTransport({ref:key.get_secret_value() for ref,key in settings.accounts.items()}),routing)
