"""Business routing for login challenges; email-only reply proof is separate."""
import re
from airport_assistant.modules.identity.auth import VerificationDeliveryUncertain
from airport_assistant.modules.mail.service import Envelope,SendResult,address


class VerificationDeliveryError(RuntimeError):
    def __init__(self):super().__init__('Verification delivery unavailable.')


class VerificationMail:
    def __init__(self,routes,transport,routing):
        self._routes=dict(routes);self._transport=transport;self._routing=routing

    async def send(self,message):
        route=self._routes.get(message.tenant_id)
        if route is None or not re.fullmatch(r'[0-9]{8}',message.code) or not 30<=message.expires_seconds<=600:
            raise VerificationDeliveryError()
        if address(message.recipient)!=message.recipient:raise VerificationDeliveryError()
        if not await self._routing.allowed(message.tenant_id,route):raise VerificationDeliveryError()
        domain=route.sender.rsplit('@',1)[1]
        envelope=Envelope(message.challenge_id,message.challenge_id,route.account_id,route.sender,message.recipient,route.reply_to,
            '<'+message.challenge_id.hex+'@'+domain+'>','mail/'+message.tenant_id.hex+'/'+message.challenge_id.hex,
            'Your airport assistant verification code',
            'Your verification code is '+message.code+'. It expires in '+str(message.expires_seconds)+' seconds.\n'
            'Enter it in the browser where you requested verification. Do not reply with this login code.\n'
            'If you did not request this code, ignore this email.',route.account_ref)
        # One POST only. Unknown receipt is not success and never triggers a resend here.
        try:result=await self._transport.send(envelope)
        except Exception:raise VerificationDeliveryUncertain() from None
        if type(result) is not SendResult or result.state=='delivery_unknown':raise VerificationDeliveryUncertain()
        if result.state!='accepted':raise VerificationDeliveryError()
