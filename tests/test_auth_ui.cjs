// Valida o painel de recuperacao e o payload enviado ao backend.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const js = fs.readFileSync(path.join(root, 'interface/app.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'interface/index.html'), 'utf8');

assert.match(html, /id="authRecoveryOpenButton"[\s\S]*?>Recuperar acesso</);
assert.match(html, /id="authRecoveryForm"[\s\S]*?onsubmit="recuperarAcesso\(event\)"/);
assert.match(html, /id="authRecoveryKeyModal"/);
assert.match(html, /id="authLoginLembrar"[\s\S]*?Lembrar senha neste computador/);
assert.match(js, /lembrar:\s*Boolean\(el\("authLoginLembrar"\)\?\.checked\)/);

const start = js.indexOf('async function recuperarAcesso(');
const end = js.indexOf('async function sairDoSistema()', start);
assert.ok(start > 0 && end > start, 'funcao recuperarAcesso ausente');
const source = js.slice(start, end);

const elements = {
  authRecoveryMatricula: {value: 'E123456'},
  authRecoveryKey: {value: 'ABCD-EFGH-JKLM-NPQR'},
  authRecoverySenha: {value: 'NovaSenha123'},
  authRecoveryConfirmacao: {value: 'NovaSenha123'},
  authLoginMatricula: {value: ''},
  authLoginSenha: {focusCalls: 0, focus() { this.focusCalls += 1; }}
};
const calls = [];
const panels = [];
const keys = [];
const successes = [];
const errors = [];
const context = {
  state: {authBusy: false},
  APP_VERSION: '1.8.11',
  MAX_MATRICULA_CARACTERES: 7,
  el: (id) => elements[id],
  setAuthBusy: (value) => { context.state.authBusy = value; },
  mostrarAuthErro: (message) => errors.push(message),
  mostrarAuthSucesso: (message) => successes.push(message),
  mostrarAuthPainel: (panel) => panels.push(panel),
  mostrarChaveRecuperacao: (key, message) => keys.push({key, message}),
  window: {
    pywebview: {
      api: {
        recuperar_acesso: async (payload) => {
          calls.push(payload);
          return {
            ok: true,
            msg: 'Senha redefinida.',
            chave_recuperacao: 'WXYZ-2345-6789-ABCD'
          };
        }
      }
    },
    setTimeout: (callback) => callback()
  }
};

vm.createContext(context);
vm.runInContext(source, context);

async function main() {
  await vm.runInContext('recuperarAcesso({preventDefault(){}})', context);

  assert.equal(calls.length, 1);
  assert.deepEqual(
    JSON.parse(JSON.stringify(calls[0])),
    {
      matricula: 'E123456',
      chave_recuperacao: 'ABCD-EFGH-JKLM-NPQR',
      nova_senha: 'NovaSenha123',
      versao_app: '1.8.11'
    }
  );
  assert.deepEqual(panels, ['login']);
  assert.equal(elements.authLoginMatricula.value, 'E123456');
  assert.equal(elements.authRecoveryKey.value, '');
  assert.equal(elements.authRecoverySenha.value, '');
  assert.equal(elements.authRecoveryConfirmacao.value, '');
  assert.equal(keys[0].key, 'WXYZ-2345-6789-ABCD');
  assert.equal(successes.at(-1), 'Senha redefinida.');
  assert.equal(errors.filter(Boolean).length, 0);
  assert.equal(context.state.authBusy, false);
  console.log('OK: recuperar acesso redefine senha, gira a chave e volta ao login.');
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
