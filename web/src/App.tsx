import { useState } from 'react'

import { SearchWorkspace } from './SearchWorkspace'
import { TokenGate } from './TokenGate'

export const App = (): React.JSX.Element => {
  const [accessToken, setAccessToken] = useState<string | null>(null)

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        본문 바로가기
      </a>
      <header className="app-header">
        <div className="content-boundary">
          <p className="product-context">CREFLE · KNOWLEDGE RETRIEVAL</p>
          <h1>OMF 정보 조회</h1>
        </div>
      </header>
      <main id="main-content" className="app-main" tabIndex={-1}>
        {accessToken === null ? (
          <TokenGate onConnected={setAccessToken} />
        ) : (
          <SearchWorkspace
            accessToken={accessToken}
            onDisconnect={() => setAccessToken(null)}
          />
        )}
      </main>
    </div>
  )
}
