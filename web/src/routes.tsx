import { Navigate, Route, Routes } from 'react-router'
import Aprovacoes from './pages/Aprovacoes'
import Caixa from './pages/Caixa'
import Chat from './pages/Chat'
import Login from './pages/Login'
import Relevantes from './pages/Relevantes'
import { RequireAuth } from './shell/RequireAuth'
import { Page, PageHead } from './ui/Page'

// Replaced by the real page in the task that builds it.
function Soon({ title }: { title: string }) {
  return (
    <Page>
      <PageHead title={title} />
    </Page>
  )
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<RequireAuth />}>
        <Route index element={<Relevantes />} />
        <Route path="caixa" element={<Caixa />} />
        <Route path="caixa/:emailId" element={<Caixa />} />
        <Route path="aprovacoes" element={<Aprovacoes />} />
        <Route path="documentos" element={<Soon title="Documentos" />} />
        <Route path="chat" element={<Chat />} />
        <Route path="agente" element={<Soon title="Agente" />} />
        <Route path="execucoes" element={<Soon title="Execuções" />} />
        <Route path="seguranca" element={<Soon title="Segurança" />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
