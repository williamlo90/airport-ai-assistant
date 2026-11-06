"""Evaluation-only transport. Gemini envelopes are normalized, never relabeled in evidence."""
import asyncio,json,re,time
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
import httpx
from airport_assistant.modules.agent.models import AgentError,strict_json

OPENAI='https://api.openai.com/v1/responses'
GEMINI='https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'


def retry_after(value):
    if not value:return None
    try:
        seconds=int(value) if value.isascii() and value.isdigit() else (parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds()
        return max(0,min(86400,seconds))
    except (ValueError,TypeError,OverflowError):return None


def network_category(error):
    # Never persist exception messages, request URLs, headers or credentials.
    return next((label for kind,label in (
        (httpx.ConnectTimeout,'connect_timeout'),(httpx.ReadTimeout,'read_timeout'),
        (httpx.WriteTimeout,'write_timeout'),(httpx.PoolTimeout,'pool_timeout'),
        (httpx.ConnectError,'connect_error'),(httpx.RemoteProtocolError,'remote_protocol_error'),
    ) if isinstance(error,kind)),'http_transport_error')


def wire(provider,canonical,config):
    if provider=='openai':return OPENAI,{**canonical,'model':config['model']}
    if provider!='gemini':raise ValueError('Unknown provider.')
    messages=[{'role':'system','content':canonical['instructions']},*canonical['input']]
    result=dict(model=config['model'],messages=messages,max_tokens=canonical['max_output_tokens'],
        reasoning_effort=config['reasoning'],n=1,stream=False,
        response_format={'type':'json_schema','json_schema':{k:v for k,v in canonical['text']['format'].items() if k!='type'}})
    if canonical['tools']:
        result.update(tools=[{'type':'function','function':{k:v for k,v in t.items() if k!='type'}} for t in canonical['tools']],
            tool_choice=canonical['tool_choice'],parallel_tool_calls=False)
    return GEMINI,result


def normalize_gemini(data,expected_envelope_model):
    if not isinstance(data,dict):raise ValueError('Invalid envelope.')
    choices=data.get('choices')
    if not isinstance(choices,list) or len(choices)!=1:raise ValueError('Invalid completion.')
    choice=choices[0]
    if not isinstance(choice,dict) or not isinstance(choice.get('message'),dict):raise ValueError('Invalid message.')
    message=choice['message']
    if message.get('refusal') or message.get('role')!='assistant':raise ValueError('Invalid completion.')
    calls=message.get('tool_calls')
    if calls:
        if len(calls)!=1 or message.get('content') not in (None,'') or choice.get('finish_reason') not in {'stop','tool_calls'}:raise ValueError('Invalid call.')
        call=calls[0]
        if not isinstance(call,dict) or not isinstance(call.get('function'),dict):raise ValueError('Invalid call.')
        if call.get('type')!='function' or not isinstance(call.get('id'),str) or not isinstance(call['function'].get('arguments'),str):raise ValueError('Invalid call.')
        output={'type':'function_call','status':'completed','call_id':call['id'],'name':call['function']['name'],'arguments':call['function']['arguments']}
    else:
        if choice.get('finish_reason')!='stop' or not isinstance(message.get('content'),str):raise ValueError('Incomplete completion.')
        output={'type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':message['content']}]}
    # Only an internal parser envelope. Reports use the actual provider/model field
    # captured below, not this Responses-shaped compatibility identifier.
    return {'model':expected_envelope_model,'status':'completed','output':[output]}


class Bridge(httpx.AsyncBaseTransport):
    def __init__(self,provider,config,key,canonical_digest,digest,record,*,network=None):
        self.provider,self.config,self.key=provider,config,key
        self.expected,self.digest,self.record=canonical_digest,digest,record
        self.network=network

    async def handle_async_request(self,request):
        canonical=strict_json(request.content)
        if str(request.url)!=OPENAI or self.digest(canonical)!=self.expected:raise AgentError(422,'EVAL_REQUEST_CHANGED')
        endpoint,body=wire(self.provider,canonical,self.config)
        start=time.perf_counter();self.record['sent']=True
        self.record['network_phase']='awaiting_response_headers'
        try:
            async with httpx.AsyncClient(timeout=15,follow_redirects=False,trust_env=False,transport=self.network) as client:
                async with client.stream('POST',endpoint,json=body,headers={'Authorization':'Bearer '+self.key}) as response:
                    self.record['http_status']=response.status_code
                    delay=retry_after(response.headers.get('retry-after'))
                    if delay is not None:self.record['retry_after_seconds']=delay
                    self.record['network_phase']='reading_response_body'
                    buffer=bytearray()
                    async for part in response.aiter_bytes():
                        buffer.extend(part)
                        if len(buffer)>262144:raise ValueError('Oversized provider response.')
            self.record['network_phase']='parsing_response'
            if self.key and self.key.encode() in buffer:raise ValueError('Credential marker in response.')
            if self.record['http_status']!=200:
                try:
                    error=strict_json(buffer).get('error',{})
                    if isinstance(error,dict) and error.get('code') in {'insufficient_quota','rate_limit_exceeded','invalid_api_key'}:
                        self.record['provider_error_code']=error['code']
                except (ValueError,TypeError,AttributeError,RecursionError):pass
                return httpx.Response(self.record['http_status'],json={'error':'provider_unavailable'})
            data=strict_json(buffer)
            if not isinstance(data,dict):raise ValueError('Invalid provider envelope.')
            observed=data.get('model')
            if not isinstance(observed,str) or not re.fullmatch(r'[A-Za-z0-9.-]{1,100}',observed):raise ValueError('Missing model identity.')
            self.record['observed_model']=observed
            if self.provider=='openai':
                if observed!=self.config['model']:
                    self.record['identity_error']=True;raise ValueError('Model changed.')
                usage=data.get('usage') or {}
                if not isinstance(usage,dict):raise ValueError('Invalid usage.')
                input_tokens=usage.get('input_tokens');output_tokens=usage.get('output_tokens')
            else:
                if not re.fullmatch(re.escape(self.config['model'])+r'(?:-[A-Za-z0-9.-]+)?',observed):
                    self.record['identity_error']=True;raise ValueError('Model changed.')
                usage=data.get('usage') or {}
                if not isinstance(usage,dict):raise ValueError('Invalid usage.')
                input_tokens=usage.get('prompt_tokens');output_tokens=usage.get('completion_tokens')
            self.record['reported_usage']=({'input':input_tokens,'output':output_tokens}
                if type(input_tokens) is int and input_tokens>0 and type(output_tokens) is int and output_tokens>=0 else None)
            normalized=normalize_gemini(data,canonical['model']) if self.provider=='gemini' else data
            self.record['envelope']='gemini_chat_to_responses' if self.provider=='gemini' else 'native_responses'
            self.record['network_phase']='completed'
            return httpx.Response(200,json=normalized)
        except asyncio.CancelledError:
            self.record['network_category']='cancelled'
            raise
        except httpx.HTTPError as error:
            self.record['network_category']=network_category(error)
            self.record['transport_error']='provider_or_envelope_failure'
            raise AgentError(503,'AGENT_MODEL_UNAVAILABLE') from None
        except (ValueError,KeyError,TypeError,AttributeError,RecursionError):
            self.record['network_category']='invalid_provider_envelope'
            self.record['transport_error']='provider_or_envelope_failure'
            raise AgentError(503,'AGENT_MODEL_UNAVAILABLE') from None
        finally:self.record['http_ms']=round((time.perf_counter()-start)*1000,3)
