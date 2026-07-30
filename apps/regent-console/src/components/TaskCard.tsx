import { useEffect, useState } from 'react'

interface TaskCardProps {
  task: Record<string, unknown>
  resolved?: boolean
  onAction: (taskId: string, approved: boolean) => void
}

export function TaskCard({ task, resolved = false, onAction }: TaskCardProps) {
  const [done, setDone] = useState(resolved)
  const taskId = String(task.id || task.human_task_id || '')
  const title = String(task.task_type || '人工任务')
  const prompt = String(task.prompt || '')

  useEffect(() => {
    if (resolved) setDone(true)
  }, [resolved])

  return (
    <div className="task-card">
      <h4>{title}</h4>
      {prompt ? <p>{prompt}</p> : null}
      {!done && taskId && (
        <div className="task-actions">
          <button className="task-btn approve" onClick={() => {
            setDone(true)
            onAction(taskId, true)
          }}>批准</button>
          <button className="task-btn reject" onClick={() => {
            setDone(true)
            onAction(taskId, false)
          }}>拒绝</button>
        </div>
      )}
      {done && <p className="task-done">已处理</p>}
      {!done && !taskId && (
        <p className="task-done">任务信息不完整，请在下方输入「批准」或「拒绝」</p>
      )}
    </div>
  )
}
