import { useNavigate, useParams } from "react-router-dom";
import { DetailPanel } from "../components/DetailPanel";

// A shareable, directly-linkable URL for a single context object's detail panel
// (e.g. from the review queue or a Slack deep link).
export function ContextStandalone() {
  const { contextId } = useParams<{ contextId: string }>();
  const navigate = useNavigate();
  if (!contextId) return null;
  return (
    <DetailPanel
      contextId={contextId}
      onClose={() => navigate(-1)}
      onNavigate={(id) => navigate(`/context/${id}`)}
    />
  );
}
