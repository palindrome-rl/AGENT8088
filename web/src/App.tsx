import { Routes, Route } from 'react-router-dom'
import { AppLayout } from '@/components/layout/AppLayout'
import ChatPage from '@/pages/ChatPage'
import ArtifactsPage from '@/pages/ArtifactsPage'
import ToolsPage from '@/pages/ToolsPage'
import SkillsPage from '@/pages/SkillsPage'
import AgentsPage from '@/pages/AgentsPage'
import McpPage from '@/pages/McpPage'
import MemoryPage from '@/pages/MemoryPage'
import SessionsPage from '@/pages/SessionsPage'
import ConfigPage from '@/pages/ConfigPage'
import DoctorPage from '@/pages/DoctorPage'
import FusionPage from '@/pages/FusionPage'
import TasksPage from '@/pages/TasksPage'

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<ChatPage />} />
        <Route path="/artifacts" element={<ArtifactsPage />} />
        <Route path="/tools" element={<ToolsPage />} />
        <Route path="/skills" element={<SkillsPage />} />
        <Route path="/agents" element={<AgentsPage />} />
        <Route path="/mcp" element={<McpPage />} />
        <Route path="/memory" element={<MemoryPage />} />
        <Route path="/sessions" element={<SessionsPage />} />
        <Route path="/config" element={<ConfigPage />} />
        <Route path="/doctor" element={<DoctorPage />} />
        <Route path="/fusion" element={<FusionPage />} />
        <Route path="/tasks" element={<TasksPage />} />
      </Route>
    </Routes>
  )
}
