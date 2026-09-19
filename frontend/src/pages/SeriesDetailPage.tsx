import { useParams } from "react-router-dom";

import { useSeriesDetail } from "../api/hooks";
import { GlossarySuggestionsTable } from "../components/GlossarySuggestionsTable";
import { JobTable } from "../components/JobTable";
import { ManualGlossaryTable } from "../components/ManualGlossaryTable";

export function SeriesDetailPage() {
  const { tvdbId } = useParams<{ tvdbId: string }>();
  const { data, isLoading } = useSeriesDetail(Number(tvdbId));

  if (isLoading) return <div>Loading&hellip;</div>;
  if (!data) return <div>Series not found.</div>;

  return (
    <>
      <div className="panel-head">
        <h2>{data.title ?? `Series #${data.tvdb_id}`}</h2>
      </div>

      <section className="panel">
        <div className="panel-head">
          <h2>Episodes</h2>
        </div>
        <JobTable jobs={data.jobs} />
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Manual glossary</h2>
        </div>
        <ManualGlossaryTable tvdbId={data.tvdb_id} entries={data.manual_glossary} />
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Auto-mined suggestions</h2>
        </div>
        <GlossarySuggestionsTable suggestions={data.auto_suggestions} tvdbId={data.tvdb_id} />
      </section>
    </>
  );
}
