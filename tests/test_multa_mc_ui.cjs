// Verifica fluxo de cliente/setor ausente de multa sem interação real com SAP.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const js = fs.readFileSync(path.join(root, 'interface/app.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'interface/index.html'), 'utf8');
assert.match(html, /selecionarCadastroTipo\('multa_contratual', 'Multa Contratual'\)/);
assert.match(js, /autoRetrySetor = false/);
assert.match(js, /if \(autoRetrySetor\) \{[\s\S]*?Área MC não confirmada no SAP/);

const start = js.indexOf('async function executarCriacaoSetoresPendentes()');
const end = js.indexOf('async function confirmarAcaoSapPendente()', start);
assert.ok(start > 0 && end > start);
const section = js.slice(start, end);
const helper = js.slice(js.indexOf('function isMultaContratual('), js.indexOf('function formatarDocumentoResumo('));
async function main() {
  const calls = [], resumed = [], statuses = [], logs=[];
  const btn = {disabled:false, textContent:''};
  const state = {
    pendingSapAction: {tipo:'setor_ausente', documento:'12345678900', tipo_documento:'cpf',
      cliente:'1000000', setores_necessarios:['MC']},
    pendingSapPayload: {tipo:'multa_contratual', modalidade:'multa_contratual',doc:'12345678900',valor:'R$ 100,00'},
  };
  const context={state, clonePlain:(o)=>JSON.parse(JSON.stringify(o)),
    el:()=>btn,setMonitorMode:()=>{},resetarEtapas:()=>{},setStatus:(...args)=>statuses.push(args),
    window:{pywebview:{api:{adicionar_setores_cliente_sap: async p=>{calls.push(p);return {ok:true};}}}},
    closeSapActionModal:()=>{},showSimpleOperationalToast:()=>{},
    log:(...args)=>logs.push(args),openLogsPopover:()=>{},
    executarFluxoMultaContratual:async (...args)=>resumed.push(args),
    executarFluxo:async()=>{throw Error('multa não deve usar fluxo de boletos normais')}
  };
  vm.createContext(context);
  vm.runInContext(helper + '\n' + section, context);
  await vm.runInContext('executarCriacaoSetoresPendentes()',context);
  assert.equal(calls.length,1);
  assert.equal(calls[0].cliente,'1000000');
  assert.equal(calls[0].setores[0], 'MC');
  assert.equal(calls[0].setores.length,1);
  assert.equal(resumed.length,1);
  assert.equal(resumed[0][1].autoRetrySetor,true);
  assert.equal(state.pendingSapAction,null);
  assert.equal(btn.disabled,false);
  console.log('OK: setor MC criado uma vez; fluxo de multa retomado com trava anti-loop.');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
