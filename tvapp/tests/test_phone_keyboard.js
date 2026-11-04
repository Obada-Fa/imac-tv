const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/keyboard.js'), 'utf8');
const {TVKeyboard, tvEdit} = vm.runInNewContext(source + ';({TVKeyboard,tvEdit})', {Intl, clearTimeout});

test('autocorrect, emoji deletion and backspace past the phone buffer', () => {
  const sentinel = '\u200b';
  for (const [before, after, erase, text] of [
    [sentinel + 'teh', sentinel + 'the ', 2, 'he '],
    [sentinel + '👩🏽‍🚀', sentinel, 1, ''],
    [sentinel, '', 1, ''],
    [sentinel, 'new', 0, 'new'],
  ]) {
    const edit = tvEdit(before, after);
    assert.equal(edit.erase, erase);
    assert.equal(edit.text, text);
  }
});

function keyboard() {
  const k = Object.create(TVKeyboard.prototype);
  k.open = (app, focus) => { k.session = {app, focus}; };
  k.close = () => { k.session = null; };
  return k;
}

test('dismissal lasts until TV focus changes', () => {
  const k = keyboard();
  k.observe('home', 'search:1', true);
  assert.equal(k.session.focus, 'search:1');
  k.close();
  k.observe('home', 'search:1', true);
  assert.equal(k.session, null);
  k.observe('home', 'search:2', true);
  assert.equal(k.session.focus, 'search:2');
  k.observe('home', null, true);
  assert.equal(k.session, null);
});

test('early edits wait for the requested search field and preserve their order', () => {
  const k = keyboard(), sent = [];
  k.dispatch = op => sent.push(op);
  k.session = {app:'home', pending:true, buffered:['a', 'b']};
  k.observe('home', 'search:1', true);
  k.session.ready = true;
  k.observe('home', 'some-other-field', true);
  assert.deepEqual(sent, []);
  k.observe('home', 'search:2', true);
  k.observe('home', 'search:2', true);
  assert.deepEqual(sent, ['a', 'b']);
  assert.equal(k.session.focus, 'search:2');
});
