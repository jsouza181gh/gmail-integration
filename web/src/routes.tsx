import { Navigate, Route, Routes } from 'react-router'
import Agente from './pages/Agente'
import Aprovacoes from './pages/Aprovacoes'
import Caixa from './pages/Caixa'
import Chat from './pages/Chat'
import Documentos from './pages/Documentos'
import Execucoes from './pages/Execucoes'
import Login from './pages/Login'
import Relevantes from './pages/Relevantes'
import Seguranca from './pages/Seguranca'
import Admin from './pages/Admin'
import Configuracoes from './pages/Configuracoes'
import Conectar from './pages/Onboarding/Conectar'
import Pronto from './pages/Onboarding/Pronto'
import { RequireAuth } from './shell/RequireAuth'

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/onboarding" element={<Conectar />} />
      <Route path="/onboarding/pronto" element={<Pronto />} />
      <Route element={<RequireAuth />}>
        <Route index element={<Relevantes />} />
        <Route path="caixa" element={<Caixa />} />
        <Route path="caixa/:emailId" element={<Caixa />} />
        <Route path="aprovacoes" element={<Aprovacoes />} />
        <Route path="documentos" element={<Documentos />} />
        <Route path="chat" element={<Chat />} />
        <Route path="agente" element={<Agente />} />
        <Route path="execucoes" element={<Execucoes />} />
        <Route path="seguranca" element={<Seguranca />} />
        <Route path="configuracoes" element={<Configuracoes />} />
        <Route path="admin" element={<Admin />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
