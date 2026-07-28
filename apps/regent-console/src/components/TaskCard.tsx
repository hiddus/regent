import { useState } from 'react'

interface TaskCardProps {
  task: Record<string, unknown>
  onAction: (taskId: string, approved: boolean) => void
}

export function TaskCard({ task, onAction }: TaskCardProps) {
  const [done, setDone] = useState(false)

  return (
    <div className="task-card">
      <h4>{(task.task_type as string) || '人工任务'}</h4>
      <p>{(task.prompt as string) || ''}</p>
      {!done && (
        <div className="task-actions">
          <button className="task-btn approve" onClick={() => {
            setDone(true)
            onAction(task.id as string, true)
          }}>批准</button>
          <button className="task-btn reject" onClick={() => {
            setDone(true)
            onAction(task.id as string, false)
          }}>拒绝</button>
        </div>
      )}
      {done && <p className="task-done">已处理</p>}
    </div>
  )
}
