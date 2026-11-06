-- Only scoped RPCs write organization memberships. Existing auth-owner row
-- locks and read helpers retain their permissions; runtime DML is removed.
REVOKE INSERT,UPDATE,DELETE ON @SCHEMA@.staff_memberships,@SCHEMA@.membership_roles,@SCHEMA@.membership_queues FROM @RUNTIME@;
REVOKE UPDATE(active,version) ON @SCHEMA@.staff_memberships FROM @RUNTIME@;
REVOKE UPDATE(role) ON @SCHEMA@.membership_roles FROM @RUNTIME@;
REVOKE UPDATE(queue_id) ON @SCHEMA@.membership_queues FROM @RUNTIME@;
GRANT SELECT ON @SCHEMA@.organizations,@SCHEMA@.service_queues,@SCHEMA@.membership_queues TO @POLICY@;
CREATE POLICY administration_metadata ON @SCHEMA@.organizations FOR SELECT TO @POLICY@ USING(true);
CREATE POLICY administration_metadata ON @SCHEMA@.service_queues FOR SELECT TO @POLICY@ USING(true);
CREATE POLICY administration_metadata ON @SCHEMA@.membership_queues FOR SELECT TO @POLICY@ USING(true);
GRANT UPDATE(id) ON @SCHEMA@.organizations TO @POLICY@;
CREATE POLICY administration_org_lock ON @SCHEMA@.organizations FOR UPDATE TO @POLICY@ USING(true) WITH CHECK(false);
GRANT UPDATE(active,version) ON @SCHEMA@.staff_memberships TO @POLICY@;
GRANT INSERT,DELETE ON @SCHEMA@.membership_roles,@SCHEMA@.membership_queues TO @POLICY@;
CREATE POLICY administration_update ON @SCHEMA@.staff_memberships FOR UPDATE TO @POLICY@ USING(true) WITH CHECK(true);
CREATE POLICY administration_insert ON @SCHEMA@.membership_roles FOR INSERT TO @POLICY@ WITH CHECK(true);
CREATE POLICY administration_delete ON @SCHEMA@.membership_roles FOR DELETE TO @POLICY@ USING(true);
CREATE POLICY administration_insert ON @SCHEMA@.membership_queues FOR INSERT TO @POLICY@ WITH CHECK(true);
CREATE POLICY administration_delete ON @SCHEMA@.membership_queues FOR DELETE TO @POLICY@ USING(true);

CREATE TABLE @SCHEMA@.membership_events(
 tenant_id uuid NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,
 actor_membership_id uuid NOT NULL,actor_principal_id uuid NOT NULL,actor_session_id uuid NOT NULL,
 target_membership_id uuid NOT NULL,target_principal_id uuid NOT NULL,
 previous_version integer NOT NULL,resulting_version integer NOT NULL,
 before_state jsonb NOT NULL,after_state jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,target_membership_id,resulting_version),CHECK(resulting_version=previous_version+1),
 CHECK(actor_principal_id<>target_principal_id),
 FOREIGN KEY(tenant_id,organization_id) REFERENCES @SCHEMA@.organizations(tenant_id,id),
 FOREIGN KEY(tenant_id,actor_membership_id,organization_id) REFERENCES @SCHEMA@.staff_memberships(tenant_id,id,organization_id),
 FOREIGN KEY(tenant_id,target_membership_id,organization_id) REFERENCES @SCHEMA@.staff_memberships(tenant_id,id,organization_id),
 FOREIGN KEY(tenant_id,actor_session_id) REFERENCES @SCHEMA@.sessions(tenant_id,id));
ALTER TABLE @SCHEMA@.membership_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE @SCHEMA@.membership_events FORCE ROW LEVEL SECURITY;
REVOKE ALL ON @SCHEMA@.membership_events FROM PUBLIC;
GRANT INSERT ON @SCHEMA@.membership_events TO @POLICY@;
CREATE POLICY administration_audit ON @SCHEMA@.membership_events FOR INSERT TO @POLICY@ WITH CHECK(true);

CREATE FUNCTION @SCHEMA@.admin_workspace(actor uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
 IF @SCHEMA@.current_actor_kind()<>'staff' OR NOT @SCHEMA@.case_staff_session_valid() THEN RETURN NULL;END IF;
 SELECT jsonb_build_object('membership_id',m.id,'membership_version',m.version,
   'organization',jsonb_build_object('id',o.id,'name',o.name),
   'queues',coalesce((SELECT jsonb_agg(jsonb_build_object('id',q.id,'name',q.name,'active',q.active) ORDER BY q.id)
      FROM @SCHEMA@.service_queues q WHERE q.tenant_id=m.tenant_id AND q.organization_id=m.organization_id),'[]'::jsonb))
 INTO result FROM @SCHEMA@.staff_memberships m
 JOIN @SCHEMA@.organizations o ON(o.tenant_id,o.id)=(m.tenant_id,m.organization_id)
 JOIN @SCHEMA@.principals p ON(p.tenant_id,p.id)=(m.tenant_id,m.principal_id)
 WHERE m.tenant_id=@SCHEMA@.current_tenant_id() AND m.id=actor AND m.principal_id=@SCHEMA@.current_principal_id()
  AND m.active AND p.active AND o.active AND EXISTS(SELECT 1 FROM @SCHEMA@.membership_roles r
    WHERE(r.tenant_id,r.membership_id)=(m.tenant_id,m.id) AND r.role='admin');
 RETURN result;
END $$;

CREATE FUNCTION @SCHEMA@.admin_member(actor uuid,principal uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE workspace jsonb;result jsonb;
BEGIN
 workspace:=@SCHEMA@.admin_workspace(actor);IF workspace IS NULL THEN RETURN NULL;END IF;
 SELECT jsonb_build_object('membership_id',m.id,'principal_id',m.principal_id,'organization_id',m.organization_id,
  'display_name',p.display_name,'principal_active',p.active,'active',m.active,'version',m.version,
  'roles',coalesce((SELECT jsonb_agg(r.role ORDER BY r.role) FROM @SCHEMA@.membership_roles r
    WHERE(r.tenant_id,r.membership_id)=(m.tenant_id,m.id)),'[]'::jsonb),
  'queue_ids',coalesce((SELECT jsonb_agg(q.queue_id ORDER BY q.queue_id) FROM @SCHEMA@.membership_queues q
    WHERE(q.tenant_id,q.membership_id)=(m.tenant_id,m.id)),'[]'::jsonb))
 INTO result FROM @SCHEMA@.staff_memberships m JOIN @SCHEMA@.principals p
 ON(p.tenant_id,p.id)=(m.tenant_id,m.principal_id)
 WHERE m.tenant_id=@SCHEMA@.current_tenant_id() AND m.organization_id=(workspace->'organization'->>'id')::uuid
   AND m.principal_id=principal;
 RETURN result;
END $$;

CREATE FUNCTION @SCHEMA@.admin_directory(actor uuid,after_principal uuid,page_limit integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE workspace jsonb;result jsonb;
BEGIN
 workspace:=@SCHEMA@.admin_workspace(actor);
 IF workspace IS NULL OR page_limit IS NULL OR page_limit NOT BETWEEN 1 AND 101 THEN RETURN NULL;END IF;
 SELECT coalesce(jsonb_agg(@SCHEMA@.admin_member(actor,m.principal_id) ORDER BY m.principal_id),'[]'::jsonb) INTO result
 FROM(SELECT principal_id FROM @SCHEMA@.staff_memberships
   WHERE tenant_id=@SCHEMA@.current_tenant_id() AND organization_id=(workspace->'organization'->>'id')::uuid
     AND(after_principal IS NULL OR principal_id>after_principal) ORDER BY principal_id LIMIT page_limit) m;
 RETURN result;
END $$;

CREATE FUNCTION @SCHEMA@.admin_change_membership(actor uuid,actor_version integer,principal uuid,
 expected_version integer,is_active boolean,new_roles text[],new_queues uuid[]) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE workspace jsonb;org uuid;target @SCHEMA@.staff_memberships%ROWTYPE;before jsonb;after jsonb;
 roles text[];queues uuid[];same boolean;
BEGIN
 workspace:=@SCHEMA@.admin_workspace(actor);
 IF workspace IS NULL THEN RETURN jsonb_build_object('status','not_found');END IF;
 IF principal=@SCHEMA@.current_principal_id() THEN RETURN jsonb_build_object('status','forbidden');END IF;
 org:=(workspace->'organization'->>'id')::uuid;
 -- All admin writes for this organization acquire this lock before either
 -- membership. Reciprocal admin changes cannot lock actor-first in reverse.
 PERFORM id FROM @SCHEMA@.organizations WHERE tenant_id=@SCHEMA@.current_tenant_id() AND id=org FOR UPDATE;
 PERFORM id FROM @SCHEMA@.staff_memberships WHERE tenant_id=@SCHEMA@.current_tenant_id() AND organization_id=org
   AND(id=actor OR principal_id=principal) ORDER BY id FOR UPDATE;
 workspace:=@SCHEMA@.admin_workspace(actor);
 IF workspace IS NULL THEN RETURN jsonb_build_object('status','not_found');END IF;
 IF actor_version IS NULL OR actor_version<>(workspace->>'membership_version')::integer THEN
   RETURN jsonb_build_object('status','actor_changed');END IF;
 SELECT * INTO target FROM @SCHEMA@.staff_memberships WHERE tenant_id=@SCHEMA@.current_tenant_id()
   AND organization_id=org AND principal_id=principal;
 IF NOT FOUND THEN RETURN jsonb_build_object('status','not_found');END IF;
 IF is_active IS NULL OR expected_version IS NULL OR expected_version<1 OR new_roles IS NULL OR new_queues IS NULL
   OR cardinality(new_roles) NOT BETWEEN 1 AND 3 OR cardinality(new_queues)>100
   OR array_position(new_roles,NULL) IS NOT NULL OR array_position(new_queues,NULL) IS NOT NULL
   OR NOT(new_roles<@ARRAY['cs','supervisor','admin']::text[])
   OR cardinality(new_roles)<>(SELECT count(DISTINCT x) FROM unnest(new_roles) x)
   OR cardinality(new_queues)<>(SELECT count(DISTINCT x) FROM unnest(new_queues) x)
   OR EXISTS(SELECT 1 FROM unnest(new_queues) AS desired(queue_uuid) WHERE NOT EXISTS(SELECT 1 FROM @SCHEMA@.service_queues q
      WHERE q.tenant_id=target.tenant_id AND q.id=desired.queue_uuid AND q.organization_id=org AND q.active)) THEN
  RETURN jsonb_build_object('status','invalid');END IF;
 SELECT array_agg(x ORDER BY x) INTO roles FROM unnest(new_roles) x;
 SELECT coalesce(array_agg(x ORDER BY x),ARRAY[]::uuid[]) INTO queues FROM unnest(new_queues) x;
 before:=@SCHEMA@.admin_member(actor,principal);
 same:=(before->>'active')::boolean=is_active AND before->'roles'=to_jsonb(roles) AND before->'queue_ids'=to_jsonb(queues);
 IF target.version<>expected_version AND NOT(same AND target.version=expected_version+1) THEN
  RETURN jsonb_build_object('status','version_conflict');END IF;
 IF NOT same THEN
  IF target.version=2147483647 THEN RETURN jsonb_build_object('status','version_conflict');END IF;
  DELETE FROM @SCHEMA@.membership_roles WHERE tenant_id=target.tenant_id AND membership_id=target.id;
  INSERT INTO @SCHEMA@.membership_roles(tenant_id,id,membership_id,role)
    SELECT target.tenant_id,gen_random_uuid(),target.id,x FROM unnest(roles) x;
  DELETE FROM @SCHEMA@.membership_queues WHERE tenant_id=target.tenant_id AND membership_id=target.id;
  INSERT INTO @SCHEMA@.membership_queues(tenant_id,id,membership_id,queue_id,organization_id)
    SELECT target.tenant_id,gen_random_uuid(),target.id,x,org FROM unnest(queues) x;
  UPDATE @SCHEMA@.staff_memberships SET active=is_active,version=version+1 WHERE tenant_id=target.tenant_id AND id=target.id;
  after:=@SCHEMA@.admin_member(actor,principal);
  INSERT INTO @SCHEMA@.membership_events(tenant_id,organization_id,actor_membership_id,actor_principal_id,actor_session_id,
    target_membership_id,target_principal_id,previous_version,resulting_version,before_state,after_state)
  VALUES(target.tenant_id,org,actor,@SCHEMA@.current_principal_id(),@SCHEMA@.current_session_id(),target.id,principal,
    target.version,target.version+1,before-ARRAY['display_name','principal_active'],after-ARRAY['display_name','principal_active']);
 ELSE after:=before;
 END IF;
 IF @SCHEMA@.admin_workspace(actor) IS DISTINCT FROM workspace THEN RAISE EXCEPTION 'Administrative scope changed' USING ERRCODE='42501';END IF;
 RETURN jsonb_build_object('status','saved','member',after);
END $$;

REVOKE ALL ON FUNCTION @SCHEMA@.admin_workspace(uuid),@SCHEMA@.admin_member(uuid,uuid),
 @SCHEMA@.admin_directory(uuid,uuid,integer),@SCHEMA@.admin_change_membership(uuid,integer,uuid,integer,boolean,text[],uuid[]) FROM PUBLIC;
ALTER FUNCTION @SCHEMA@.admin_workspace(uuid) OWNER TO @POLICY@;
ALTER FUNCTION @SCHEMA@.admin_member(uuid,uuid) OWNER TO @POLICY@;
ALTER FUNCTION @SCHEMA@.admin_directory(uuid,uuid,integer) OWNER TO @POLICY@;
ALTER FUNCTION @SCHEMA@.admin_change_membership(uuid,integer,uuid,integer,boolean,text[],uuid[]) OWNER TO @POLICY@;
GRANT EXECUTE ON FUNCTION @SCHEMA@.admin_workspace(uuid),@SCHEMA@.admin_member(uuid,uuid),
 @SCHEMA@.admin_directory(uuid,uuid,integer),@SCHEMA@.admin_change_membership(uuid,integer,uuid,integer,boolean,text[],uuid[]) TO @RUNTIME@;
