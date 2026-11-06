"""Provider-controlled HTTP diagnostics never enter general application logs."""
import logging


class RedactHttpLog(logging.Filter):
    def filter(self, record):
        record.msg = 'provider_http_event (details redacted)'
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def protect_http_logs():
    for name in ('httpx', 'httpcore', 'httpcore.connection', 'httpcore.connection_pool', 'httpcore.http11', 'httpcore.http2', 'httpcore.proxy', 'httpcore.socks'):
        logger = logging.getLogger(name)
        if not any(isinstance(item, RedactHttpLog) for item in logger.filters):
            logger.addFilter(RedactHttpLog())
