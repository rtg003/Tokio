import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

export default async function ConfigPage() {
  const supabase = await createClient();
  const { data: exchanges } = await supabase
    .from("exchanges")
    .select("id, name, network, status")
    .order("id");

  return (
    <section>
      <div className="pagehead">
        <div>
          <div className="eyebrow">Sistema</div>
          <h1>Configurações · Corretoras</h1>
        </div>
      </div>

      <div className="settings-grid">
        <div className="card">
          <div className="cardhead">
            <div className="exhead">
              <span className="exlogo">HL</span>
              <h2 style={{ margin: 0 }}>Hyperliquid</h2>
            </div>
            <span className="cardnote">gateway ativo · SDK oficial · rede default: testnet</span>
          </div>

          <div className="walletbar">
            <span className="t">
              Contas registradas — a mudança para MAINNET é gate humano fora da web
              (o toggle abaixo é somente leitura)
            </span>
          </div>
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>Corretora</th>
                  <th>Ambiente</th>
                  <th>Agent wallets</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {(exchanges ?? []).length === 0 ? (
                  <tr>
                    <td className="addr" colSpan={4}>
                      nenhuma corretora registrada — o gateway popula esta tabela ao subir
                    </td>
                  </tr>
                ) : (
                  (exchanges ?? []).map((e) => (
                    <tr key={e.id}>
                      <td>{e.name}</td>
                      <td>
                        <span className="envtoggle">
                          <span className={e.network === "testnet" ? "on-test" : ""}>
                            TESTNET
                          </span>
                          <span className={e.network === "mainnet" ? "on-main" : ""}>
                            MAINNET
                          </span>
                        </span>
                      </td>
                      <td>
                        <span className="chip ack">engine_gateway</span>{" "}
                        <span className="chip ack">hermes_ops · exp.</span>
                      </td>
                      <td>
                        <span className={`chip ${e.status === "active" ? "live" : "dry"}`}>
                          {String(e.status).toUpperCase()}
                        </span>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          <div className="formrow" style={{ borderTop: "1px solid var(--line)" }}>
            <label>Endereço da conta</label>
            <div>
              <input className="input" disabled placeholder="definido via .env (HL_ACCOUNT_ADDRESS)" />
              <div className="hint">
                endereço público da master account (usado para consultas e atribuição)
              </div>
            </div>
          </div>
          <div className="formrow">
            <label>Agent wallet · chave privada</label>
            <div>
              <input className="input" type="password" disabled placeholder="••••••••••••••••" />
              <div className="hint">
                <b>armazenada apenas no .env do servidor</b> — nunca no banco, nunca no
                navegador · agent sem permissão de saque
              </div>
            </div>
          </div>
          <div className="formrow">
            <label>Ambiente</label>
            <div>
              <span className="envtoggle">
                <span className="on-test">TESTNET</span>
                <span>MAINNET</span>
              </span>
              <div className="hint">
                mainnet exige confirmação humana + checklist de segurança (gate
                permanente — não ativável pela web)
              </div>
            </div>
          </div>
          <div className="formrow">
            <label>Subaccounts (Fase B)</label>
            <div>
              <span className="chip dry">BLOQUEADAS</span>
              <div className="hint">
                desbloqueiam após US$ 100k de volume acumulado — buckets por
                risco/módulo, assinadas pelo gateway (ADR 0002)
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
