import { ThemeProvider } from './theme/ThemeContext'
import ChatPage from './pages/ChatPage'

/** The app opens straight into the RAG workspace - there is no login. */
export default function App() {
  return (
    <ThemeProvider>
      <ChatPage />
    </ThemeProvider>
  )
}
