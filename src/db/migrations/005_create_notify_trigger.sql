-- pg_notify trigger for n8n integration
-- Sends a notification when new scraped_data rows are inserted

CREATE OR REPLACE FUNCTION notify_new_scraped_data()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM pg_notify('new_scraped_data', json_build_object(
        'id', NEW.id,
        'job_id', NEW.job_id,
        'domain', NEW.domain,
        'data_type', NEW.data_type,
        'url', NEW.url
    )::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS scraped_data_notify ON scraped_data;

CREATE TRIGGER scraped_data_notify
    AFTER INSERT ON scraped_data
    FOR EACH ROW
    EXECUTE FUNCTION notify_new_scraped_data();
