import { redirect } from "next/navigation";

// This standalone app serves the UI under /sediment (the nested layout's nav
// and the community-era links all hardcode /sediment/*). Root just forwards.
export default function Home() {
  redirect("/sediment");
}
