-- One row per judgment/order PDF published by a court.
CREATE TABLE IF NOT EXISTS judgments (
    id                   BIGSERIAL PRIMARY KEY,
    source               TEXT        NOT NULL,           -- e.g. 'aws-hc-judgments'
    court                TEXT        NOT NULL,           -- e.g. 'Rajasthan High Court'
    bench                TEXT        NOT NULL,           -- 'jaipur' | 'jodhpur'
    cnr                  TEXT,                           -- eCourts Case Number Record
    pdf_link             TEXT        NOT NULL UNIQUE,    -- path as published by the court
    pdf_key              TEXT        NOT NULL,           -- object key in the source bucket
    case_type            TEXT,                           -- e.g. 'CW', 'CRLMB'
    case_number          INTEGER,
    case_year            INTEGER,
    title                TEXT        NOT NULL,
    petitioner           TEXT,
    respondent           TEXT,
    judges               TEXT[]      NOT NULL DEFAULT '{}',
    bench_strength       TEXT,                           -- 'single' | 'division' | 'full'
    disposal_nature      TEXT,
    date_of_registration DATE,
    decision_date        DATE,
    description          TEXT,                           -- opening lines of the judgment
    full_text            TEXT,                           -- extracted from the PDF, when fetched
    text_extracted_at    TIMESTAMPTZ,
    imported_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    search               TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(description, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(full_text, '')), 'C')
    ) STORED
);

CREATE INDEX IF NOT EXISTS judgments_search_idx        ON judgments USING GIN (search);
CREATE INDEX IF NOT EXISTS judgments_judges_idx        ON judgments USING GIN (judges);
CREATE INDEX IF NOT EXISTS judgments_decision_date_idx ON judgments (decision_date DESC);
CREATE INDEX IF NOT EXISTS judgments_case_idx          ON judgments (case_type, case_year, case_number);
CREATE INDEX IF NOT EXISTS judgments_cnr_idx           ON judgments (cnr);

-- Structured fields pulled from full_text by law_help.extract (see `importer structure`).
ALTER TABLE judgments ADD COLUMN IF NOT EXISTS text_language    TEXT;   -- 'en' | 'hi' | 'hi-krutidev' | 'no-text'
ALTER TABLE judgments ADD COLUMN IF NOT EXISTS neutral_citation TEXT;   -- e.g. '2024:RJ-JP:2823'
ALTER TABLE judgments ADD COLUMN IF NOT EXISTS parties          JSONB;  -- {"petitioners": [...], "respondents": [...]}
ALTER TABLE judgments ADD COLUMN IF NOT EXISTS advocates        JSONB;  -- {"petitioner": [...], "respondent": [...]}
ALTER TABLE judgments ADD COLUMN IF NOT EXISTS bench_judges     TEXT[]; -- as printed on the judgment
ALTER TABLE judgments ADD COLUMN IF NOT EXISTS acts_cited       JSONB;  -- [{"act": ..., "sections": [...]}]
ALTER TABLE judgments ADD COLUMN IF NOT EXISTS cases_cited      JSONB;  -- [{"name": ..., "citations": [...]}]
ALTER TABLE judgments ADD COLUMN IF NOT EXISTS summary          TEXT;
ALTER TABLE judgments ADD COLUMN IF NOT EXISTS outcome          TEXT;   -- 'Bail granted', 'Dismissed', ... from the closing lines
ALTER TABLE judgments ADD COLUMN IF NOT EXISTS key_reasoning    TEXT;   -- long judgments only: the court's reasons, copied
ALTER TABLE judgments ADD COLUMN IF NOT EXISTS extractor_version INTEGER;
ALTER TABLE judgments ADD COLUMN IF NOT EXISTS structured_at    TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS judgments_acts_cited_idx       ON judgments USING GIN (acts_cited jsonb_path_ops);
CREATE INDEX IF NOT EXISTS judgments_cases_cited_idx      ON judgments USING GIN (cases_cited jsonb_path_ops);
CREATE INDEX IF NOT EXISTS judgments_neutral_citation_idx ON judgments (neutral_citation);
CREATE INDEX IF NOT EXISTS judgments_bench_judges_idx     ON judgments USING GIN (bench_judges);

-- What `importer update` has already imported: one row per source file, with its ETag.
CREATE TABLE IF NOT EXISTS source_partitions (
    key           TEXT        PRIMARY KEY,           -- object key in the source bucket
    source        TEXT        NOT NULL,
    etag          TEXT        NOT NULL,
    last_modified TIMESTAMPTZ NOT NULL,
    rows          INTEGER     NOT NULL,
    imported_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Text extraction (`importer text`): why a PDF gave no text, and how far each archive got.
ALTER TABLE judgments ADD COLUMN IF NOT EXISTS text_error TEXT;  -- parse failure, or 'PDF not in the dataset'
CREATE INDEX IF NOT EXISTS judgments_text_pending_idx
    ON judgments (decision_date DESC NULLS LAST, id) WHERE text_extracted_at IS NULL;

CREATE TABLE IF NOT EXISTS text_archives (
    key         TEXT        PRIMARY KEY,   -- data/tar/.../data.tar or part-*.tar
    etag        TEXT        NOT NULL,      -- a new ETag means a new file: start over
    next_offset BIGINT      NOT NULL,      -- byte offset of the first member not yet written
    done        BOOLEAN     NOT NULL DEFAULT false,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Model-written summary from law_help.summarize: {"summary", "issues", "holding", "outcome"}.
-- The extractive `summary` above stays as the fallback until this is filled.
ALTER TABLE judgments ADD COLUMN IF NOT EXISTS ai_summary       JSONB;
ALTER TABLE judgments ADD COLUMN IF NOT EXISTS ai_summary_model TEXT;
ALTER TABLE judgments ADD COLUMN IF NOT EXISTS ai_summarized_at TIMESTAMPTZ;

-- "Cited by": this court's cases a judgment cites (law_help.extract.case_refs), e.g.
-- 'CW/6863/2014', 'CRLAS|CRLA/12/2020' (either type code), 'NC:2024:RJ-JP:2823'.
ALTER TABLE judgments ADD COLUMN IF NOT EXISTS case_refs TEXT[];

-- Those refs resolved to judgments in this database, rebuilt whole by `importer citations`
-- (so no foreign keys: it is derived data, and they would block TRUNCATE judgments).
CREATE TABLE IF NOT EXISTS citations (
    citing_id BIGINT NOT NULL,   -- judgments.id
    cited_id  BIGINT NOT NULL,   -- judgments.id
    PRIMARY KEY (cited_id, citing_id)
);
CREATE INDEX IF NOT EXISTS citations_citing_idx ON citations (citing_id);
-- How the citing judgment treats the one it cites (law_help.treatment), labelled with the links:
-- 'followed' | 'distinguished' | 'doubted' | 'cited', and the citing judgment's own words.
ALTER TABLE citations ADD COLUMN IF NOT EXISTS treatment       TEXT NOT NULL DEFAULT 'cited';
ALTER TABLE citations ADD COLUMN IF NOT EXISTS treatment_quote TEXT;

-- Hindi typed in the legacy Kruti Dev font: full_text holds the Unicode conversion, and this
-- the text as extracted, so `importer hindi` can re-convert it when the converter improves.
ALTER TABLE judgments ADD COLUMN IF NOT EXISTS full_text_original TEXT;

-- Good law check: a judgment a later judgment of this court set aside, recalled or overruled,
-- with the later judgment's own words. Rebuilt whole by `importer goodlaw` (law_help.goodlaw).
CREATE TABLE IF NOT EXISTS treatments (
    judgment_id BIGINT NOT NULL,   -- judgments.id of the order set aside / overruled
    by_id       BIGINT NOT NULL,   -- judgments.id of the later judgment
    kind        TEXT   NOT NULL,   -- 'set_aside' | 'partly_set_aside' | 'recalled' | 'overruled'
    quote       TEXT   NOT NULL,
    PRIMARY KEY (judgment_id, by_id)
);

-- Supreme Court judgments (law_help.supreme): court = 'Supreme Court of India', bench = 'supreme court',
-- neutral_citation '2024 INSC 735' from the metadata, and the Supreme Court Reports citation here.
ALTER TABLE judgments ADD COLUMN IF NOT EXISTS report_citation TEXT;  -- e.g. '[2024] 10 S.C.R. 108'
CREATE INDEX IF NOT EXISTS judgments_court_idx ON judgments (court, decision_date DESC);

-- How many different later judgments cite each judgment (law_help.landmark), rebuilt with
-- `citations`. The most cited are shown as landmarks.
CREATE TABLE IF NOT EXISTS cited_counts (
    judgment_id BIGINT  PRIMARY KEY,   -- judgments.id
    n           INTEGER NOT NULL
);

-- The fewest citations that make a judgment a landmark, per court (law_help.landmark).
CREATE TABLE IF NOT EXISTS landmark_thresholds (
    court TEXT    PRIMARY KEY,
    n     INTEGER NOT NULL
);

-- Search feeds (/feed): when a judgment first reached law_help, so a daily update's new judgments
-- come first. Not imported_at, which every re-import of a partition resets. Rows that predate
-- the column are backfilled once from decision_date; new rows get now() and upserts leave it alone.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'judgments' AND column_name = 'added_at'
                     AND table_schema = current_schema()) THEN
        ALTER TABLE judgments ADD COLUMN added_at TIMESTAMPTZ;
        UPDATE judgments SET added_at = coalesce(decision_date::timestamptz, imported_at);
        ALTER TABLE judgments ALTER COLUMN added_at SET DEFAULT now();
        ALTER TABLE judgments ALTER COLUMN added_at SET NOT NULL;
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS judgments_added_at_idx ON judgments (added_at DESC, id DESC);

-- Slow counts (topics, judgments per section) kept across restarts; law_help.counts
CREATE TABLE IF NOT EXISTS stored_counts (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
