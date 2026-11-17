-- Native dataset inspection/preview runs in the query workload. Semantic View
-- lifecycle writes retain the web/agent owner; data access still uses Ranger.
GRANT EXECUTE ON FUNCTION nova_meta.intelligence_actor_bound() TO nova_meta_query;
GRANT SELECT ON nova_meta.config_semantic_views,
    nova_meta.config_semantic_view_versions,nova_meta.config_entities TO nova_meta_query;

CREATE POLICY semantic_views_query_read ON nova_meta.config_semantic_views
    FOR SELECT TO nova_meta_query USING (
        nova_meta.intelligence_actor_bound() AND status='ACTIVE'
        AND (visibility='PUBLIC' OR owner_name=current_setting('nova.principal',true))
    );
CREATE POLICY semantic_versions_query_read ON nova_meta.config_semantic_view_versions
    FOR SELECT TO nova_meta_query USING (
        nova_meta.intelligence_actor_bound() AND status IN ('ACTIVE','DEPRECATED')
        AND EXISTS (SELECT 1 FROM nova_meta.config_semantic_views v WHERE v.id=view_id)
    );
CREATE POLICY entities_query_read ON nova_meta.config_entities
    FOR SELECT TO nova_meta_query USING (nova_meta.intelligence_actor_bound());
