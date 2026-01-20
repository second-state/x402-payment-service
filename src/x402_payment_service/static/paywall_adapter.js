(function() {
  'use strict';

  const TOKEN = window.__x402_token || { symbol: 'TOKEN', address: '', decimals: 18, name: '', version: '1' };
  const DISPLAY_AMOUNT = window.__x402_display_amount || 0;
  const PERMIT_DEADLINE = 3600;

  if (!TOKEN.address) return;

  if (window.x402?.paymentRequirements?.[0]?.extra) {
    const extra = window.x402.paymentRequirements[0].extra;
    TOKEN.name = TOKEN.name || extra.name || TOKEN.symbol;
    TOKEN.version = TOKEN.version || extra.version || '1';
  }

  function formatAmount(atomic, decimals) {
    const d = BigInt(10 ** decimals);
    const i = (BigInt(atomic) / d).toString();
    const f = (BigInt(atomic) % d).toString().padStart(decimals, '0').slice(0, 4).replace(/0+$/, '');
    return f ? `${i}.${f}` : i;
  }

  async function getBalance() {
    if (!window.ethereum) return null;
    try {
      const accounts = await window.ethereum.request({ method: 'eth_accounts' });
      if (!accounts?.length) return null;
      const data = '0x70a08231' + accounts[0].slice(2).padStart(64, '0');
      const result = await window.ethereum.request({ method: 'eth_call', params: [{ to: TOKEN.address, data }, 'latest'] });
      return formatAmount(result, TOKEN.decimals);
    } catch { return null; }
  }

  async function getNonce(owner) {
    const result = await window.ethereum.request({
      method: 'eth_call',
      params: [{ to: TOKEN.address, data: '0x7ecebe00' + owner.slice(2).padStart(64, '0') }, 'latest']
    });
    return BigInt(result);
  }

  function replaceText() {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);

    nodes.forEach(node => {
      const parent = node.parentNode;
      if (!parent || parent.tagName === 'SCRIPT' || parent.tagName === 'STYLE') return;

      let t = node.nodeValue;
      if (!t?.trim()) return;

      let changed = false;
      if (t.includes('USDC')) { t = t.replace(/USDC/g, TOKEN.symbol); changed = true; }
      if (/\$[\d,]+(?:\.\d+)?/.test(t) || /[\d,]{7,}/.test(t)) {
        const p = new RegExp(`\\$[\\d,]+(?:\\.\\d+)?(?:\\s*${TOKEN.symbol})?|\\$?[\\d,]{7,}(?:\\.\\d+)?(?:\\s*${TOKEN.symbol})?`, 'g');
        t = t.replace(p, `${DISPLAY_AMOUNT} ${TOKEN.symbol}`);
        changed = true;
      }
      if (changed) node.nodeValue = t;
    });
  }

  async function updateBalance() {
    const balance = await getBalance();
    if (!balance) return;
    document.querySelectorAll('[class*="balance"], .balance-button').forEach(el => {
      if (el.textContent?.includes('USDC') || el.textContent?.includes('•')) {
        el.textContent = `${balance} ${TOKEN.symbol}`;
      }
    });
  }

  let processing = false;
  function update() {
    if (processing) return;
    processing = true;
    try { replaceText(); } finally { processing = false; }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { update(); setTimeout(updateBalance, 1000); });
  } else {
    update(); setTimeout(updateBalance, 1000);
  }

  new MutationObserver(() => setTimeout(update, 100)).observe(document.body, { childList: true, subtree: true });

  if (window.ethereum) {
    const origRequest = window.ethereum.request.bind(window.ethereum);
    window.ethereum.request = async function(args) {
      if (args.method !== 'eth_signTypedData_v4') return origRequest(args);

      try {
        const params = JSON.parse(args.params[1]);
        if (params.primaryType !== 'TransferWithAuthorization') return origRequest(args);

        window.__x402_useEip2612 = true;
        const { from, to, value } = params.message;
        const nonce = await getNonce(from);
        const deadline = Math.floor(Date.now() / 1000) + PERMIT_DEADLINE;

        const permitParams = {
          types: {
            EIP712Domain: [
              { name: "name", type: "string" }, { name: "version", type: "string" },
              { name: "chainId", type: "uint256" }, { name: "verifyingContract", type: "address" }
            ],
            Permit: [
              { name: "owner", type: "address" }, { name: "spender", type: "address" },
              { name: "value", type: "uint256" }, { name: "nonce", type: "uint256" },
              { name: "deadline", type: "uint256" }
            ]
          },
          primaryType: "Permit",
          domain: { name: TOKEN.name, version: TOKEN.version, chainId: params.domain.chainId, verifyingContract: TOKEN.address },
          message: { owner: from, spender: to, value, nonce: nonce.toString(), deadline: deadline.toString() }
        };

        const sig = await origRequest({ method: 'eth_signTypedData_v4', params: [args.params[0], JSON.stringify(permitParams)] });
        window.__x402_permitData = { owner: from, spender: to, value, nonce: Number(nonce), deadline, signature: sig, to, amount: value };
        return sig;
      } catch { return origRequest(args); }
    };
  }

  const origFetch = window.fetch;
  window.fetch = async function(...args) {
    const [, opts] = args;
    if (!opts?.headers?.['X-PAYMENT'] && !opts?.headers?.['x-payment']) return origFetch.apply(this, args);

    const header = opts.headers['X-PAYMENT'] || opts.headers['x-payment'];
    try {
      const data = JSON.parse(atob(header));
      if (window.__x402_permitData && window.__x402_useEip2612 && data.payload?.authorization) {
        const p = window.__x402_permitData;
        const req = window.x402?.paymentRequirements?.[0];
        data.payload = {
          permit: { owner: p.owner, spender: req?.payTo || p.spender, value: p.value.toString(), deadline: p.deadline.toString(), nonce: p.nonce, signature: p.signature },
          transfer: { from: p.owner, to: req?.payTo || p.to, amount: p.amount.toString() }
        };
        const newHeader = btoa(JSON.stringify(data));
        opts.headers['X-PAYMENT'] = newHeader;
        if (opts.headers['x-payment']) opts.headers['x-payment'] = newHeader;
        window.__x402_permitData = null;
        window.__x402_useEip2612 = false;
      }
    } catch {}
    return origFetch.apply(this, args);
  };
})();
