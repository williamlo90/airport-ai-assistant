CREATE TABLE @SCHEMA@.customer_case_updates(
  tenant_id uuid NOT NULL,id uuid NOT NULL,case_id uuid NOT NULL,principal_id uuid NOT NULL,session_id uuid NOT NULL,
  request_key text NOT NULL CHECK(request_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$'),
  request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),message_id uuid NOT NULL,
  message text NOT NULL CHECK(length(message) BETWEEN 1 AND 4000 AND message ~ '[^[:space:]]'),
  language text NOT NULL CHECK(language IN('en','id')),expected_version bigint NOT NULL CHECK(expected_version>0),
  preview_expires_at timestamptz NOT NULL,confirmed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  resource_security_labels jsonb NOT NULL CHECK(jsonb_typeof(resource_security_labels)='array'),
  PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,principal_id,request_key),UNIQUE(tenant_id,message_id),
  FOREIGN KEY(tenant_id,case_id) REFERENCES @SCHEMA@.cases(tenant_id,id),
  FOREIGN KEY(tenant_id,principal_id) REFERENCES @SCHEMA@.principals(tenant_id,id),
  FOREIGN KEY(tenant_id,session_id) REFERENCES @SCHEMA@.sessions(tenant_id,id),
  FOREIGN KEY(tenant_id,message_id) REFERENCES @SCHEMA@.messages(tenant_id,id) DEFERRABLE INITIALLY DEFERRED);
ALTER TABLE @SCHEMA@.customer_case_updates ENABLE ROW LEVEL SECURITY;
ALTER TABLE @SCHEMA@.customer_case_updates FORCE ROW LEVEL SECURITY;
REVOKE ALL ON @SCHEMA@.customer_case_updates FROM PUBLIC;
GRANT SELECT ON @SCHEMA@.customer_case_updates TO @RUNTIME@;
GRANT INSERT(tenant_id,id,case_id,principal_id,session_id,request_key,request_hash,message_id,message,language,
  expected_version,preview_expires_at,resource_security_labels) ON @SCHEMA@.customer_case_updates TO @RUNTIME@;
CREATE POLICY customer_update_read ON @SCHEMA@.customer_case_updates FOR SELECT TO @RUNTIME@ USING(
  tenant_id=@SCHEMA@.current_tenant_id() AND principal_id=@SCHEMA@.current_principal_id()
  AND @SCHEMA@.current_actor_kind()='customer' AND @SCHEMA@.conversation_session_valid()
  AND @SCHEMA@.customer_case_ancestry(case_id) IS NOT NULL);
CREATE POLICY customer_update_insert ON @SCHEMA@.customer_case_updates FOR INSERT TO @RUNTIME@ WITH CHECK(
  tenant_id=@SCHEMA@.current_tenant_id() AND principal_id=@SCHEMA@.current_principal_id()
  AND session_id=@SCHEMA@.current_session_id() AND @SCHEMA@.current_actor_kind()='customer'
  AND @SCHEMA@.conversation_session_valid() AND @SCHEMA@.customer_case_ancestry(case_id) IS NOT NULL);

CREATE FUNCTION @SCHEMA@.guard_customer_update() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE c @SCHEMA@.cases%ROWTYPE;labels jsonb;BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Customer update is immutable' USING ERRCODE='23514';END IF;
  SELECT * INTO c FROM @SCHEMA@.cases WHERE tenant_id=NEW.tenant_id AND id=NEW.case_id FOR UPDATE;
  labels:=@SCHEMA@.customer_case_ancestry(NEW.case_id);
  IF NOT FOUND OR c.owner_principal_id IS DISTINCT FROM @SCHEMA@.current_principal_id()
    OR c.status='closed' OR c.version<>NEW.expected_version OR labels IS NULL
    OR NEW.principal_id IS DISTINCT FROM c.owner_principal_id OR NEW.session_id IS DISTINCT FROM @SCHEMA@.current_session_id()
    OR NEW.preview_expires_at<=clock_timestamp() OR NEW.preview_expires_at>clock_timestamp()+interval '15 minutes'
    OR NEW.resource_security_labels IS DISTINCT FROM labels
    OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.tenants t WHERE t.id=NEW.tenant_id AND NEW.language=ANY(t.supported_languages)) THEN
    RAISE EXCEPTION 'Customer update context changed' USING ERRCODE='23514';END IF;
  NEW.confirmed_at:=clock_timestamp();RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION @SCHEMA@.guard_customer_update() FROM PUBLIC;
CREATE TRIGGER customer_update_guard BEFORE INSERT OR UPDATE OR DELETE ON @SCHEMA@.customer_case_updates
  FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.guard_customer_update();

CREATE FUNCTION @SCHEMA@.guard_customer_information_message() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE u @SCHEMA@.customer_case_updates%ROWTYPE;c @SCHEMA@.cases%ROWTYPE;seq bigint;BEGIN
  IF NEW.source_kind<>'customer_update' THEN RETURN NEW;END IF;
  SELECT * INTO c FROM @SCHEMA@.cases WHERE tenant_id=NEW.tenant_id AND id=NEW.case_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Customer update case unavailable' USING ERRCODE='23514';END IF;
  SELECT * INTO u FROM @SCHEMA@.customer_case_updates WHERE tenant_id=NEW.tenant_id AND message_id=NEW.id;
  IF NOT FOUND OR u.case_id IS DISTINCT FROM NEW.case_id OR u.id IS DISTINCT FROM NEW.client_message_id
    OR u.principal_id IS DISTINCT FROM NEW.author_principal_id OR u.session_id IS DISTINCT FROM NEW.created_session_id
    OR u.message IS DISTINCT FROM NEW.body OR u.language IS DISTINCT FROM NEW.language
    OR u.expected_version IS DISTINCT FROM NEW.basis_case_version OR u.request_hash IS DISTINCT FROM NEW.request_hash
    OR u.expected_version<>c.version OR c.status='closed'
    OR NEW.resource_security_labels IS DISTINCT FROM u.resource_security_labels THEN
    RAISE EXCEPTION 'Customer update message mismatch' USING ERRCODE='23514';END IF;
  SELECT coalesce(max(sequence),0)+1 INTO seq FROM @SCHEMA@.messages WHERE tenant_id=NEW.tenant_id AND case_id=NEW.case_id;
  IF NEW.sequence IS DISTINCT FROM seq THEN RAISE EXCEPTION 'Customer update sequence mismatch' USING ERRCODE='23514';END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION @SCHEMA@.guard_customer_information_message() FROM PUBLIC;
CREATE TRIGGER customer_information_message BEFORE INSERT ON @SCHEMA@.messages FOR EACH ROW
  EXECUTE FUNCTION @SCHEMA@.guard_customer_information_message();

CREATE FUNCTION @SCHEMA@.customer_information_pair(case_uuid uuid,previous bigint,resulting bigint) RETURNS boolean
LANGUAGE sql SET search_path=pg_catalog AS $$
 SELECT resulting=previous+1 AND EXISTS(
   SELECT 1 FROM @SCHEMA@.customer_case_updates u JOIN @SCHEMA@.messages m ON(m.tenant_id,m.id)=(u.tenant_id,u.message_id)
   JOIN @SCHEMA@.case_history h ON(h.tenant_id,h.case_id,h.client_request_id)=(u.tenant_id,u.case_id,u.id)
   WHERE u.tenant_id=@SCHEMA@.current_tenant_id() AND u.case_id=case_uuid AND u.expected_version=previous
     AND u.principal_id=@SCHEMA@.current_principal_id() AND u.session_id=@SCHEMA@.current_session_id()
     AND h.event_type='customer_information' AND h.previous_version=previous AND h.resulting_version=resulting
     AND h.actor_principal_id=u.principal_id AND h.actor_session_id IS NULL AND h.customer_message=u.message AND h.request_hash=u.request_hash
     AND h.from_status=h.to_status AND h.chat_transition_id IS NULL AND h.email_source_id IS NULL
     AND m.source_kind='customer_update' AND m.case_id=u.case_id AND m.conversation_id IS NULL
     AND m.author_type='customer' AND m.author_principal_id=u.principal_id AND m.author_session_id IS NULL
     AND m.created_session_id=u.session_id AND m.client_message_id=u.id AND m.body=u.message AND m.language=u.language
     AND m.request_hash=u.request_hash AND m.basis_case_version=u.expected_version
     AND m.resource_security_labels=u.resource_security_labels AND h.resource_security_labels=u.resource_security_labels)
$$;
REVOKE ALL ON FUNCTION @SCHEMA@.customer_information_pair(uuid,bigint,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION @SCHEMA@.customer_information_pair(uuid,bigint,bigint) TO @RUNTIME@;

CREATE FUNCTION @SCHEMA@.validate_customer_information() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE u @SCHEMA@.customer_case_updates%ROWTYPE;c @SCHEMA@.cases%ROWTYPE;BEGIN
  IF TG_TABLE_NAME='messages' THEN
    IF NEW.source_kind<>'customer_update' THEN RETURN NULL;END IF;
    SELECT * INTO u FROM @SCHEMA@.customer_case_updates WHERE tenant_id=NEW.tenant_id AND message_id=NEW.id;
  ELSIF TG_TABLE_NAME='case_history' THEN
    IF NEW.event_type<>'customer_information' THEN RETURN NULL;END IF;
    SELECT * INTO u FROM @SCHEMA@.customer_case_updates WHERE tenant_id=NEW.tenant_id AND id=NEW.client_request_id;
  ELSE SELECT * INTO u FROM @SCHEMA@.customer_case_updates WHERE tenant_id=NEW.tenant_id AND id=NEW.id;END IF;
  IF NOT FOUND OR NOT @SCHEMA@.customer_information_pair(u.case_id,u.expected_version,u.expected_version+1) THEN
    RAISE EXCEPTION 'Incomplete customer information graph' USING ERRCODE='23514';END IF;
  SELECT * INTO c FROM @SCHEMA@.cases WHERE tenant_id=u.tenant_id AND id=u.case_id;
  IF NOT FOUND OR u.preview_expires_at<=clock_timestamp()
    OR c.version<>u.expected_version+1 OR c.status='closed' OR c.owner_principal_id IS DISTINCT FROM u.principal_id
    OR @SCHEMA@.customer_case_ancestry(c.id) IS DISTINCT FROM u.resource_security_labels THEN
    RAISE EXCEPTION 'Customer information case mismatch' USING ERRCODE='23514';END IF;
  RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION @SCHEMA@.validate_customer_information() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER customer_information_graph AFTER INSERT ON @SCHEMA@.customer_case_updates
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.validate_customer_information();
CREATE CONSTRAINT TRIGGER customer_information_graph AFTER INSERT ON @SCHEMA@.messages
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.validate_customer_information();
CREATE CONSTRAINT TRIGGER customer_information_graph AFTER INSERT ON @SCHEMA@.case_history
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.validate_customer_information();
