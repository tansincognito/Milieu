import type { ContextObjectOut } from "../types";
import { AuthorityBadge, ConfidenceBadge, DueDateInline, StatusTag } from "./Badges";
import { formatActor } from "../format";

export function ObjectRow({
  obj,
  onClick,
}: {
  obj: ContextObjectOut;
  onClick: (id: string) => void;
}) {
  return (
    <div className="obj-row" onClick={() => onClick(obj.id)}>
      <div className="obj-main">
        <div className="content">{obj.content}</div>
        <div className="meta">
          <StatusTag status={obj.status} />
          <AuthorityBadge authority={obj.authority} />
          <ConfidenceBadge confidence={obj.confidence} />
          <span>{obj.subject_key}</span>
          {obj.stage && <span>· {obj.stage}</span>}
          <span>· {formatActor(obj.actor_label)}</span>
          <DueDateInline obj={obj} />
        </div>
      </div>
    </div>
  );
}
