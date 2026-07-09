import { http, createConfig } from "wagmi";
import { arbitrum, arbitrumSepolia } from "wagmi/chains";
import { injected } from "wagmi/connectors";

// Configuração wagmi para login SIWE (P1) e, mais adiante, assinatura EIP-712
// de `approveAgent` (P2). Cadeias:
//  - arbitrumSepolia (0x66eee): rede que a MetaMask usa p/ assinar txns HL
//    testnet (o SDK hardcoda signatureChainId=0x66eee p/ ambos ambientes).
//  - arbitrum (0xa4b1): mantida como opção p/ o V2 mainnet, caso a decisão
//    final exija chain-switch (D6). Para SIWE (personal_sign/EIP-191) a cadeia
//    é irrelevante — só declaramos para o wagmi ter transports válidos.
//
// Connector `injected`: MetaMask e afins expõem window.ethereum. Sem
// WalletConnect/projectId por ora — login é operado na mesma máquina.

export const wagmiConfig = createConfig({
  chains: [arbitrumSepolia, arbitrum],
  connectors: [injected()],
  transports: {
    [arbitrumSepolia.id]: http(),
    [arbitrum.id]: http(),
  },
  ssr: true,
});

declare module "wagmi" {
  interface Register {
    config: typeof wagmiConfig;
  }
}
