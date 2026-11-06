"""Immutable typed source snapshots for human-reviewed private persistence.

Not a public request schema and never a current-grant capability. Source DTOs
must come from the trusted read connector; every later use checks grants again.
"""
from dataclasses import dataclass, field
from decimal import Decimal
import hashlib
import json
from uuid import UUID
from zoneinfo import ZoneInfo

from .labels import SecurityLabels, merge_labels, labels_for_observation, labels_for_policy
from .models import Booking, Transaction, Policy


MAX_BYTES = 32768


def encode(value):
    text = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Source evidence exceeds the bounded snapshot size.')
    return text


def facts(value):
    if isinstance(value, dict):
        return {key: facts(item) for key, item in value.items() if key != 'observed_at'}
    if isinstance(value, list):
        return [facts(item) for item in value]
    return value


def valid_text(value):
    if isinstance(value, str):
        if '\0' in value:
            raise ValueError('Invalid source text.')
        value.encode('utf-8')
    elif isinstance(value, dict):
        for key, item in value.items():
            valid_text(key)
            valid_text(item)
    elif isinstance(value, list):
        for item in value:
            valid_text(item)


@dataclass(frozen=True)
class ObservedSource:
    data: Booking | Transaction | Policy = field(repr=False)
    booking: Booking | None = field(default=None, repr=False)


@dataclass(frozen=True)
class SourceEvidence:
    tenant_id: UUID
    records_json: str = field(repr=False)
    labels: SecurityLabels = field(repr=False)
    fact_hash: str
    basis_hash: str

    @classmethod
    def capture(cls, tenant_id, observations):
        if not isinstance(tenant_id, UUID) or type(observations) is not tuple or not 1 <= len(observations) <= 8:
            raise ValueError('Bounded trusted observations required.')
        records, bundles, booking_facts = [], [], {}
        for item in observations:
            if type(item) is not ObservedSource:
                raise ValueError('Typed source observation required.')
            data = item.data
            if type(data) in {Booking, Transaction}:
                if item.booking is not None:
                    raise ValueError('Unexpected policy binding.')
                kind = 'booking' if type(data) is Booking else 'transaction'
                bundles.append(labels_for_observation(tenant_id, data))
                record = dict(schema_version=1, kind=kind, data=data.model_dump(mode='json'))
            elif type(data) is Policy and type(item.booking) is Booking:
                bundles.append(labels_for_policy(tenant_id, item.booking, data))
                record = dict(schema_version=1, kind='policy', booking=item.booking.model_dump(mode='json'),
                              data=data.model_dump(mode='json'))
            else:
                raise ValueError('Policy requires its observed booking.')
            book = data if type(data) is Booking else item.booking
            if book is not None:
                fingerprint = encode(facts(book.model_dump(mode='json')))
                if book.reference in booking_facts and booking_facts[book.reference] != fingerprint:
                    raise ValueError('Source changed between observation reads.')
                booking_facts[book.reference] = fingerprint
            records.append(record)
        return cls.from_records(tenant_id, records, expected_labels=merge_labels(*bundles))

    @classmethod
    def from_records(cls, tenant_id, records, *, expected_labels):
        """Internal repository decoder; recompute requirements, never trust supplied labels."""
        if (not isinstance(tenant_id, UUID) or type(records) is not list or not 1 <= len(records) <= 8
                or type(expected_labels) is not SecurityLabels or expected_labels.tenant_id != tenant_id):
            raise ValueError('Labeled source evidence required.')
        try:
            valid_text(records)
            encode(records)
            bundles, seen, booking_facts = [], {}, {}
            for record in records:
                if type(record) is not dict or type(record.get('schema_version')) is not int or record['schema_version'] != 1:
                    raise ValueError()
                kind = record.get('kind')
                model = {'booking': Booking, 'transaction': Transaction, 'policy': Policy}.get(kind)
                keys = {'schema_version', 'kind', 'data'} | ({'booking'} if kind == 'policy' else set())
                if model is None or set(record) != keys:
                    raise ValueError()
                data = model.model_validate_json(encode(record['data']))
                canonical = dict(schema_version=1, kind=kind, data=data.model_dump(mode='json'))
                book = data if kind == 'booking' else None
                if kind == 'policy':
                    book = Booking.model_validate_json(encode(record['booking']))
                    canonical['booking'] = book.model_dump(mode='json')
                    bundle = labels_for_policy(tenant_id, book, data)
                else:
                    bundle = labels_for_observation(tenant_id, data)
                if book is not None:
                    fingerprint = encode(facts(book.model_dump(mode='json')))
                    if book.reference in booking_facts and booking_facts[book.reference] != fingerprint:
                        raise ValueError('Conflicting booking bindings.')
                    booking_facts[book.reference] = fingerprint
                key = (kind, data.reference, book.reference if kind == 'policy' else '')
                if key in seen:
                    previous = seen[key]
                    if facts(previous) != facts(canonical):
                        raise ValueError('Conflicting observations.')
                    # Exact duplicate facts have one review entry, with the latest
                    # observation time; no content or required scope is dropped.
                    previous_time = model.model_validate_json(encode(previous['data'])).observed_at
                    prior_binding_time = Booking.model_validate_json(encode(previous['booking'])).observed_at if kind == 'policy' else previous_time
                    current_binding_time = book.observed_at if kind == 'policy' else data.observed_at
                    if (data.observed_at,current_binding_time) > (previous_time,prior_binding_time):
                        seen[key] = canonical
                else:
                    seen[key] = canonical
                bundles.append(bundle)
            normalized = [seen[key] for key in sorted(seen)]
            labels = merge_labels(*bundles)
            if merge_labels(expected_labels, labels).to_records() != expected_labels.to_records():
                raise ValueError('Stored labels cannot weaken the source snapshot.')
            wire = encode(normalized)
            fact_wire = encode(dict(records=facts(normalized), labels=labels.to_records()))
            basis_wire = encode(dict(records=normalized, labels=labels.to_records()))
            return cls(tenant_id, wire, labels, hashlib.sha256(fact_wire.encode()).hexdigest(),
                       hashlib.sha256(basis_wire.encode()).hexdigest())
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise ValueError('Invalid stored source evidence.') from None

    def to_records(self):
        return json.loads(self.records_json)

    def display(self, language):
        if language not in {'en', 'id'}:
            raise ValueError('Supported review language required.')
        en = language == 'en'
        def booking_lines(value):
            zone = ZoneInfo(value.service_timezone)
            return ['Booking: '+value.reference, 'Status: '+value.status,
                ('Service: ' if en else 'Layanan: ')+value.service_type,
                ('Policy: ' if en else 'Kebijakan: ')+value.policy_reference,
                ('Location: ' if en else 'Lokasi: ')+value.location,
                ('From: ' if en else 'Mulai: ')+value.start_at.astimezone(zone).strftime('%Y-%m-%d %H:%M')+' '+value.service_timezone,
                ('Until: ' if en else 'Sampai: ')+value.end_at.astimezone(zone).strftime('%Y-%m-%d %H:%M')+' '+value.service_timezone]
        lines = ['Source information checked for this request:' if en else 'Informasi sumber yang diperiksa untuk pengajuan ini:']
        for record in self.to_records():
            kind, data = record['kind'], record['data']
            lines.append('')
            if kind == 'booking':
                value = Booking.model_validate_json(encode(data))
                lines.extend(booking_lines(value))
            elif kind == 'transaction':
                value = Transaction.model_validate_json(encode(data))
                lines.extend([('Transaction: ' if en else 'Transaksi: ')+value.reference, 'Status: '+value.status])
                if value.booking_reference is not None:
                    lines.append('Booking: '+value.booking_reference)
                if value.amount_minor is not None:
                    amount = Decimal(value.amount_minor) / (10 ** value.currency_exponent)
                    lines.append(('Amount: ' if en else 'Nominal: ')+value.currency+' '+format(amount, f'.{value.currency_exponent}f'))
                    lines.append(('Refund status: ' if en else 'Status refund: ')+value.refund_status)
                else:
                    lines.append('Payment details are not included in this access.' if en else 'Rincian pembayaran tidak termasuk dalam akses ini.')
            else:
                value = Policy.model_validate_json(encode(data))
                bound = Booking.model_validate_json(encode(record['booking']))
                lines.append('Booking covered by this policy:' if en else 'Booking yang dicakup kebijakan ini:')
                lines.extend(booking_lines(bound))
                lines.extend([('Booking checked: ' if en else 'Booking diperiksa: ')+bound.observed_at.isoformat().replace('+00:00','Z'),
                    ('Booking source updated: ' if en else 'Sumber booking diperbarui: ')+bound.source_updated_at.isoformat().replace('+00:00','Z'),
                    value.reference+' — v'+str(value.version),
                    ('Effective from: ' if en else 'Berlaku mulai: ')+value.effective_from.isoformat().replace('+00:00','Z'),
                    value.instructions[language]])
            lines.append(('Checked: ' if en else 'Diperiksa: ')+value.observed_at.isoformat().replace('+00:00', 'Z'))
            lines.append(('Source updated: ' if en else 'Sumber diperbarui: ')+value.source_updated_at.isoformat().replace('+00:00', 'Z'))
        return '\n'.join(lines)
