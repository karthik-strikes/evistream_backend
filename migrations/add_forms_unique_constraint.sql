-- Add unique constraint on (project_id, form_name) to prevent duplicate form names within a project.
-- This closes a race condition where concurrent requests could create forms with the same name.

ALTER TABLE forms
ADD CONSTRAINT uq_forms_project_id_form_name UNIQUE (project_id, form_name);
