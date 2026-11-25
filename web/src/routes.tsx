import { Navigate, Route, Routes } from 'react-router'
import Agente from './pages/Agente'
import Aprovacoes from './pages/Aprovacoes'
import Caixa from './pages/Caixa'
import Chat from './pages/Chat'
import Documentos from './pages/Documentos'
import Execucoes from './pages/Execucoes'
import Entrar from './pages/Entrada/Entrar'
import NaoConvidado from './pages/Entrada/NaoConvidado'
import Login from './pages/Login'
import Onboarding from './pages/Onboarding'
import LlmKey from './pages/Onboarding/LlmKey'
import Relevantes from './pages/Relevantes'
import Seguranca from './pages/Seguranca'
import { RequireAuth } from './shell/RequireAuth'

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/entrar" element={<Entrar />} />
      <Route path="/nao-convidado" element={<NaoConvidado />} />
      <Route path="/onboarding" element={<Onboarding />} />
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
        <Route path="onboarding/chave" element={<LlmKey />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
