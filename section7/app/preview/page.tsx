import { redirect } from "next/navigation";

// R1 preview index → the run screen. The three preview routes are /preview/run,
// /preview/diff and /preview/review; a bare /preview visit means the run.
export default function PreviewIndex() {
  redirect("/preview/run");
}
