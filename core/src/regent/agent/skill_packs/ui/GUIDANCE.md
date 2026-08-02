# UI Skill

- Every primary button/form must call a real backend route (create/list/update).
- Empty states beat fake cards; show “暂无数据” until the first write succeeds.
- Keep templates/static assets inside planned_paths; avoid SPA framework sprawl unless the Goal requires it.
- Prefer server-rendered HTML or lightweight JS over unimplemented React shells.
- Preview/smoke must hit a route that proves the UI is served by the live app process.
