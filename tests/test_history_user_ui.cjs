// Valida a identificação visual e a limpeza do histórico ao trocar de usuário.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const js = fs.readFileSync(path.join(root, 'interface/app.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'interface/index.html'), 'utf8');

assert.match(html, /id="historyUserScope"/);

const loginStart = js.indexOf('async function concluirLogin(');
const loginEnd = js.indexOf('async function entrarNoSistema(', loginStart);
const logoutStart = js.indexOf('async function sairDoSistema(');
const logoutEnd = js.indexOf('async function inicializarAutenticacao(', logoutStart);
assert.ok(loginStart > 0 && loginEnd > loginStart, 'funcao concluirLogin ausente');
assert.ok(logoutStart > 0 && logoutEnd > logoutStart, 'funcao sairDoSistema ausente');

const elements = {
  authUserLabel: {textContent: ''},
  historyUserScope: {textContent: ''},
  authLoginSenha: {value: 'segredo'},
  authCadastroSenha: {value: 'segredo'},
  authCadastroConfirmacao: {value: 'segredo'}
};
const context = {
  state: {
    usuarioAutenticado: null,
    flowRunning: false,
    batchRunning: false,
    extratoRunning: false,
    saldoModelos: ['modelo'],
    saldoModelosCarregados: true,
    rascunhosBackendSincronizado: true,
    historicoItens: [{id: 'registro-antigo'}],
    historicoSelecionado: {id: 'registro-antigo'}
  },
  el: (id) => elements[id] || null,
  bloquearInterfacePorLogin: () => {},
  mostrarAuthErro: () => {},
  mostrarAuthSucesso: () => {},
  inicializarDadosAposLogin: async () => {},
  setStatus: () => {},
  showSimpleOperationalToast: () => {},
  mostrarChaveRecuperacao: () => {},
  mostrarAuthPainel: () => {},
  window: {
    confirm: () => true,
    pywebview: {api: {sair_usuario: async () => ({ok: true})}}
  }
};

vm.createContext(context);
vm.runInContext(js.slice(loginStart, loginEnd), context);
vm.runInContext(js.slice(logoutStart, logoutEnd), context);

async function main() {
  await vm.runInContext(
    'concluirLogin({usuario: {matricula: "e123456"}})',
    context
  );
  assert.equal(elements.authUserLabel.textContent, 'Matricula e123456 · Sair');
  assert.equal(
    elements.historyUserScope.textContent,
    'Somente os registros da matricula e123456'
  );

  await vm.runInContext('sairDoSistema()', context);
  assert.equal(context.state.usuarioAutenticado, null);
  assert.equal(context.state.historicoItens.length, 0);
  assert.equal(context.state.historicoSelecionado, null);
  console.log('OK: histórico identifica a matrícula e é limpo ao sair.');
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
