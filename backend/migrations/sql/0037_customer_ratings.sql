CREATE TABLE @SCHEMA@.ratings(
 tenant_id uuid NOT NULL,id uuid NOT NULL,case_id uuid,conversation_id uuid,
 score integer NOT NULL CHECK(score BETWEEN 1 AND 5),
 comment text CHECK(comment IS NULL OR(length(comment) BETWEEN 1 AND 2000 AND comment ~ '[^[:space:]]')),
 version integer NOT NULL CHECK(version>0),created_session_id uuid NOT NULL,updated_session_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 resource_security_labels jsonb NOT NULL DEFAULT '[]' CHECK(@SCHEMA@.source_labels_valid(resource_security_labels)),
 PRIMARY KEY(tenant_id,id),CHECK((case_id IS NULL)<>(conversation_id IS NULL)),
 FOREIGN KEY(tenant_id,case_id) REFERENCES @SCHEMA@.cases(tenant_id,id),
 FOREIGN KEY(tenant_id,conversation_id) REFERENCES @SCHEMA@.conversations(tenant_id,id),
 FOREIGN KEY(tenant_id,created_session_id) REFERENCES @SCHEMA@.sessions(tenant_id,id),
 FOREIGN KEY(tenant_id,updated_session_id) REFERENCES @SCHEMA@.sessions(tenant_id,id));
CREATE UNIQUE INDEX rating_one_case ON @SCHEMA@.ratings(tenant_id,case_id) WHERE case_id IS NOT NULL;
CREATE UNIQUE INDEX rating_one_conversation ON @SCHEMA@.ratings(tenant_id,conversation_id) WHERE conversation_id IS NOT NULL;
ALTER TABLE @SCHEMA@.ratings ENABLE ROW LEVEL SECURITY;
ALTER TABLE @SCHEMA@.ratings FORCE ROW LEVEL SECURITY;
REVOKE ALL ON @SCHEMA@.ratings FROM PUBLIC;

CREATE TABLE @SCHEMA@.rating_events(
 tenant_id uuid NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),rating_id uuid NOT NULL,
 resulting_version integer NOT NULL CHECK(resulting_version>0),actor_principal_id uuid,actor_session_id uuid NOT NULL,
 score integer NOT NULL CHECK(score BETWEEN 1 AND 5),comment_hash text,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,rating_id,resulting_version),
 FOREIGN KEY(tenant_id,rating_id) REFERENCES @SCHEMA@.ratings(tenant_id,id),
 FOREIGN KEY(tenant_id,actor_session_id) REFERENCES @SCHEMA@.sessions(tenant_id,id));
ALTER TABLE @SCHEMA@.rating_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE @SCHEMA@.rating_events FORCE ROW LEVEL SECURITY;
REVOKE ALL ON @SCHEMA@.rating_events FROM PUBLIC;
GRANT INSERT ON @SCHEMA@.rating_events TO @POLICY@;
CREATE POLICY audit_append ON @SCHEMA@.rating_events FOR INSERT TO @POLICY@ WITH CHECK(true);

CREATE FUNCTION @SCHEMA@.rating_target(kind text,target uuid,take_lock boolean DEFAULT false) RETURNS jsonb
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE c @SCHEMA@.cases%ROWTYPE;v @SCHEMA@.conversations%ROWTYPE;labels jsonb;eligible boolean;
BEGIN
 IF NOT @SCHEMA@.conversation_session_valid() OR @SCHEMA@.current_actor_kind() NOT IN('anonymous','customer') THEN RETURN NULL;END IF;
 IF kind='case' THEN
  IF @SCHEMA@.current_actor_kind()<>'customer' THEN RETURN NULL;END IF;
  IF take_lock THEN
   SELECT * INTO c FROM @SCHEMA@.cases WHERE tenant_id=@SCHEMA@.current_tenant_id() AND id=target
    AND owner_principal_id=@SCHEMA@.current_principal_id() FOR UPDATE;
  ELSE
   SELECT * INTO c FROM @SCHEMA@.cases WHERE tenant_id=@SCHEMA@.current_tenant_id() AND id=target
    AND owner_principal_id=@SCHEMA@.current_principal_id();
  END IF;
  IF NOT FOUND THEN RETURN NULL;END IF;
  labels:=@SCHEMA@.customer_case_ancestry(c.id);
  IF labels IS NULL THEN RETURN NULL;END IF;
  RETURN jsonb_build_object('version',c.version,'eligible',c.status='closed','resource_security_labels',labels);
 ELSIF kind='conversation' THEN
  IF take_lock THEN
   SELECT * INTO v FROM @SCHEMA@.conversations WHERE tenant_id=@SCHEMA@.current_tenant_id() AND id=target
    AND case_id IS NULL AND((@SCHEMA@.current_actor_kind()='customer' AND customer_principal_id=@SCHEMA@.current_principal_id())
      OR(@SCHEMA@.current_actor_kind()='anonymous' AND anonymous_session_id=@SCHEMA@.current_session_id())) FOR UPDATE;
  ELSE
   SELECT * INTO v FROM @SCHEMA@.conversations WHERE tenant_id=@SCHEMA@.current_tenant_id() AND id=target
    AND case_id IS NULL AND((@SCHEMA@.current_actor_kind()='customer' AND customer_principal_id=@SCHEMA@.current_principal_id())
      OR(@SCHEMA@.current_actor_kind()='anonymous' AND anonymous_session_id=@SCHEMA@.current_session_id()));
  END IF;
  IF NOT FOUND THEN RETURN NULL;END IF;
  eligible:=v.mode='ended' OR EXISTS(SELECT 1 FROM @SCHEMA@.messages WHERE tenant_id=v.tenant_id
    AND conversation_id=v.id AND source_kind IN('ai_response','staff_reply'));
  RETURN jsonb_build_object('version',v.version,'eligible',eligible,'resource_security_labels',v.resource_security_labels);
 END IF;
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION @SCHEMA@.rating_target(text,uuid,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION @SCHEMA@.rating_target(text,uuid,boolean) TO @RUNTIME@;

CREATE FUNCTION @SCHEMA@.guard_customer_rating() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE parent jsonb;labels jsonb;
BEGIN
 IF NEW.tenant_id IS DISTINCT FROM @SCHEMA@.current_tenant_id()
   OR NEW.updated_session_id IS DISTINCT FROM @SCHEMA@.current_session_id() THEN
  RAISE EXCEPTION 'Invalid rating actor' USING ERRCODE='42501';END IF;
 parent:=@SCHEMA@.rating_target(CASE WHEN NEW.case_id IS NOT NULL THEN 'case' ELSE 'conversation' END,
   coalesce(NEW.case_id,NEW.conversation_id),true);
 IF parent IS NULL OR (parent->>'eligible')::boolean IS NOT TRUE THEN
  RAISE EXCEPTION 'Rating unavailable' USING ERRCODE='42501';END IF;
 labels:=parent->'resource_security_labels';
 IF NOT @SCHEMA@.source_labels_valid(labels) OR(@SCHEMA@.current_actor_kind()='anonymous' AND labels<>'[]'::jsonb) THEN
  RAISE EXCEPTION 'Invalid rating classification' USING ERRCODE='42501';END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.version<>1 OR NEW.created_session_id<>@SCHEMA@.current_session_id() THEN
   RAISE EXCEPTION 'Invalid initial rating' USING ERRCODE='23514';END IF;
  NEW.created_at:=clock_timestamp();
 ELSE
  IF (NEW.tenant_id,NEW.id,NEW.case_id,NEW.conversation_id,NEW.created_session_id,NEW.created_at)
    IS DISTINCT FROM(OLD.tenant_id,OLD.id,OLD.case_id,OLD.conversation_id,OLD.created_session_id,OLD.created_at)
    OR NEW.version<>OLD.version+1 OR (NEW.score,NEW.comment) IS NOT DISTINCT FROM(OLD.score,OLD.comment)
    OR NOT @SCHEMA@.source_labels_contains(labels,OLD.resource_security_labels) THEN
   RAISE EXCEPTION 'Invalid rating revision' USING ERRCODE='23514';END IF;
 END IF;
 NEW.resource_security_labels:=labels;NEW.updated_at:=clock_timestamp();RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION @SCHEMA@.guard_customer_rating() FROM PUBLIC;
CREATE TRIGGER rating_guard BEFORE INSERT OR UPDATE ON @SCHEMA@.ratings
 FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.guard_customer_rating();

CREATE FUNCTION @SCHEMA@.audit_customer_rating() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 INSERT INTO @SCHEMA@.rating_events(tenant_id,rating_id,resulting_version,actor_principal_id,actor_session_id,score,comment_hash)
 VALUES(NEW.tenant_id,NEW.id,NEW.version,@SCHEMA@.current_principal_id(),@SCHEMA@.current_session_id(),NEW.score,
   CASE WHEN NEW.comment IS NULL THEN NULL ELSE encode(sha256(convert_to(NEW.comment,'UTF8')),'hex') END);
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION @SCHEMA@.audit_customer_rating() FROM PUBLIC;
ALTER FUNCTION @SCHEMA@.audit_customer_rating() OWNER TO @POLICY@;
CREATE TRIGGER rating_audit AFTER INSERT OR UPDATE ON @SCHEMA@.ratings
 FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.audit_customer_rating();

GRANT SELECT ON @SCHEMA@.ratings TO @RUNTIME@;
GRANT INSERT(tenant_id,id,case_id,conversation_id,score,comment,version,created_session_id,updated_session_id)
 ON @SCHEMA@.ratings TO @RUNTIME@;
GRANT UPDATE(score,comment,version,updated_session_id) ON @SCHEMA@.ratings TO @RUNTIME@;
CREATE POLICY rating_read ON @SCHEMA@.ratings FOR SELECT TO @RUNTIME@ USING(tenant_id=@SCHEMA@.current_tenant_id()
 AND @SCHEMA@.rating_target(CASE WHEN case_id IS NOT NULL THEN 'case' ELSE 'conversation' END,coalesce(case_id,conversation_id),false) IS NOT NULL);
CREATE POLICY rating_insert ON @SCHEMA@.ratings FOR INSERT TO @RUNTIME@ WITH CHECK(tenant_id=@SCHEMA@.current_tenant_id()
 AND @SCHEMA@.rating_target(CASE WHEN case_id IS NOT NULL THEN 'case' ELSE 'conversation' END,coalesce(case_id,conversation_id),false) IS NOT NULL);
CREATE POLICY rating_update ON @SCHEMA@.ratings FOR UPDATE TO @RUNTIME@
 USING(tenant_id=@SCHEMA@.current_tenant_id() AND @SCHEMA@.rating_target(CASE WHEN case_id IS NOT NULL THEN 'case' ELSE 'conversation' END,coalesce(case_id,conversation_id),false) IS NOT NULL)
 WITH CHECK(tenant_id=@SCHEMA@.current_tenant_id() AND @SCHEMA@.rating_target(CASE WHEN case_id IS NOT NULL THEN 'case' ELSE 'conversation' END,coalesce(case_id,conversation_id),false) IS NOT NULL);
