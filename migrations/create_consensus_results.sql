-- Migration: create_consensus_results
-- Creates a dedicated table for consensus review results,
-- replacing the hack of using extraction_results with extraction_type='consensus'.

CREATE TABLE IF NOT EXISTS public.consensus_results (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES public.projects(id)  ON DELETE CASCADE,
    form_id         UUID NOT NULL REFERENCES public.forms(id)     ON DELETE CASCADE,
    document_id     UUID NOT NULL REFERENCES public.documents(id) ON DELETE CASCADE,
    review_mode     VARCHAR(20) NOT NULL DEFAULT 'ai_only',  -- 'ai_only' | 'ai_manual'
    field_decisions JSONB NOT NULL DEFAULT '{}',             -- per-field decision objects
    agreed_count    INT  NOT NULL DEFAULT 0,
    disputed_count  INT  NOT NULL DEFAULT 0,
    total_fields    INT  NOT NULL DEFAULT 0,
    agreement_pct   INT,                                     -- null until computed
    created_by      UUID REFERENCES public.users(id),
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT consensus_results_unique_doc UNIQUE (project_id, form_id, document_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_cr_project_form ON public.consensus_results(project_id, form_id);
CREATE INDEX IF NOT EXISTS idx_cr_document     ON public.consensus_results(document_id);

-- RLS (backend uses service key → bypasses; policies protect direct anon access)
ALTER TABLE public.consensus_results ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE tablename = 'consensus_results' AND policyname = 'cr_owner_all'
  ) THEN
    CREATE POLICY "cr_owner_all" ON public.consensus_results FOR ALL
      USING (project_id IN (SELECT id FROM public.projects WHERE user_id = auth.uid()));
  END IF;
END
$$;
