import { useSessionStore } from '@/stores/session'

export function PlanFlowchart() {
  const { planSteps } = useSessionStore()
  if (!planSteps.length) return null

  const steps = [...planSteps].sort((a, b) => a.index - b.index)
  const nodeWidth = 180
  const nodeHeight = 50
  const gap = 80
  const svgWidth = nodeWidth
  const svgHeight = steps.length * (nodeHeight + gap) - gap

  const statusColors: Record<string, string> = {
    pending: '#71717a',
    running: '#00edff',
    done: '#22c55e',
    failed: '#ef4444',
  }

  return (
    <div className="mx-4 my-2 overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-4">
      <svg width={svgWidth} height={svgHeight} className="mx-auto">
        {steps.map((step, i) => {
          const y = i * (nodeHeight + gap)
          const color = statusColors[step.status] || statusColors.pending
          return (
            <g key={step.index}>
              {i > 0 && (
                <line
                  x1={nodeWidth / 2}
                  y1={y - gap}
                  x2={nodeWidth / 2}
                  y2={y}
                  stroke="#3f3f46"
                  strokeWidth="1.5"
                  markerEnd="url(#arrow)"
                />
              )}
              <rect
                x={0}
                y={y}
                width={nodeWidth}
                height={nodeHeight}
                rx={8}
                fill="#18181b"
                stroke={color}
                strokeWidth="1.5"
              />
              <text
                x={nodeWidth / 2}
                y={y + 20}
                textAnchor="middle"
                fill={color}
                fontSize="11"
                fontFamily="monospace"
              >
                {step.toolName}
              </text>
              <text
                x={nodeWidth / 2}
                y={y + 36}
                textAnchor="middle"
                fill="#71717a"
                fontSize="9"
              >
                {step.stepText.slice(0, 22)}{step.stepText.length > 22 ? '...' : ''}
              </text>
            </g>
          )
        })}
        <defs>
          <marker id="arrow" markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto">
            <path d="M0,0 L0,8 L8,4 Z" fill="#3f3f46" />
          </marker>
        </defs>
      </svg>
    </div>
  )
}