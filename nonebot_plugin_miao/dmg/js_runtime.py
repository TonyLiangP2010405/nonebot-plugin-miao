# ruff: noqa: E501
"""QuickJS runtime used by :mod:`nonebot_plugin_miao.dmg.service`.

The character ``calc.js`` files are the upstream miao-plugin rules.  Keeping
them as JavaScript is important: they contain hundreds of character-specific
closures and are updated alongside the metadata package.  This runtime only
provides the stable calculation boundary (attributes, buffs and ``dmg``
callbacks); character rules themselves are never translated to Python.
"""

# Deliberately ES5-ish: quickjs accepts modern syntax, while the small surface
# here makes it straightforward to audit the data crossing the Python/JS edge.
RUNTIME = r'''
var lodash = {
  forEach: function (obj, fn) { if (!obj) return; Object.keys(obj).forEach(function (k) { fn(obj[k], k) }) },
  isFunction: function (v) { return typeof v === 'function' },
  isString: function (v) { return typeof v === 'string' },
  isUndefined: function (v) { return typeof v === 'undefined' },
  merge: function (out) { out = out || {}; for (var i = 1; i < arguments.length; i++) { var x = arguments[i] || {}; Object.keys(x).forEach(function (k) { if (x[k] && typeof x[k] === 'object' && !Array.isArray(x[k])) out[k] = lodash.merge(out[k] || {}, x[k]); else out[k] = x[k] }); } return out }
};
var Format = { elemName: function (x) { return x || '' }, comma: function (x) { return String(Math.round(x * 10) / 10) } };
function _item(x) { return x || { base: 0, plus: 0, pct: 0, inc: 0 } }
function _value(x) { x = _item(x); return (+x.base || 0) * (1 + (+x.pct || 0) / 100) + (+x.plus || 0) }
function _clone(x) { return JSON.parse(JSON.stringify(x)) }
function _blankAttr(p) {
  var a = _clone(p.staticAttr || {}), names = 'atk,def,hp,speed,mastery,recharge,cpct,cdmg,heal,dmg,phy,coloringDmg,enemydmg,stance,effPct,effDef,joy'.split(',');
  names.forEach(function (k) { a[k] = _item(a[k]) });
  'a,a2,a3,e,e2,xe,q,q2,q3,t,t2,me,me2,mt,mt2,dot,break,elation,nightsoul'.split(',').forEach(function (k) { a[k] = { pct: 0, multi: 0, plus: 0, dmg: 0, enemydmg: 0, cpct: 0, cdmg: 0, def: 0, ignore: 0 } });
  a.enemy = { def: 0, ignore: 0, phy: 0 }; a.superBreak = { ignore: 0 }; a.kx = 0; a.fykx = 0; a.multi = 0;
  a.vaporize = a.melt = a.aggravate = a.spread = a.fyplus = a.fypct = a.fybase = a.fyinc = 0;
  a.element = p.element || ''; a.weaponTypeName = p.weaponTypeName || ''; a.refine = Math.max(0, (+p.refine || 1) - 1);
  a.staticAttr = _clone(p.staticAttr || {}); return a;
}
function _add(a, key, val) {
  if (typeof val === 'function') return; val = +val || 0;
  var m = /^(a|a2|a3|e|e2|q|q2|q3|t|t2|me|xe|xe2|mt|dot|break|nightsoul)(Def|Ignore|Dmg|Enemydmg|Plus|Pct|Cpct|Cdmg|Multi)$/.exec(key);
  if (m) { a[m[1]][m[2].toLowerCase()] += val; return }
  m = /^(mastery|cpct|cdmg|heal|recharge|dmg|enemydmg|phy|coloringDmg|shield|speed|stance|joy)(Plus|Pct|Inc)?$/.exec(key);
  if (m) { a[m[1]] = _item(a[m[1]]); a[m[1]][m[2] ? m[2].toLowerCase() : 'plus'] += val; return }
  m = /^(hp|def|atk)(Base|Plus|Pct)?$/.exec(key);
  if (m) { a[m[1]] = _item(a[m[1]]); a[m[1]][m[2] ? m[2].toLowerCase() : 'plus'] += val; return }
  if (key === 'enemyDef') a.enemy.def += val;
  else if (key === 'ignore' || key === 'enemyIgnore') a.enemy.ignore += val;
  else if (Object.prototype.hasOwnProperty.call(a, key)) a[key] += val;
}
function _ds(a, p, params, talent) {
  var attr = {};
  Object.keys(a).forEach(function (k) { attr[k] = a[k] && typeof a[k] === 'object' && ('base' in a[k]) ? _value(a[k]) : a[k] });
  attr.staticAttr = a.staticAttr; attr.enemy = a.enemy; attr.superBreak = a.superBreak;
  return { attr: attr, params: params || {}, talent: talent, cons: +p.cons || 0, trees: p.trees || {}, level: +p.level || 90, element: a.element, weapon: p.weapon || {}, weaponTypeName: a.weaponTypeName, refine: a.refine, calc: _value };
}
function _buffed(p, buffs, params, talent) {
  var a = _blankAttr(p), active = [];
  (buffs || []).slice().sort(function (x, y) { return (+x.sort || 1) - (+y.sort || 1) }).forEach(function (b) {
    if (typeof b === 'string') { if (b === 'aggravate' || b === 'spread') b = { title: b, mastery: b }; else return }
    if (!b || b.isStatic || (+b.cons && (+p.cons || 0) < +b.cons) || (typeof b.maxCons !== 'undefined' && (+p.cons || 0) > +b.maxCons)) return;
    var ds = _ds(a, p, params, talent); if (b.check && !b.check(ds)) return; if (b.tree && !ds.trees['10' + b.tree]) return;
    var data = b.data || {}; Object.keys(data).forEach(function (k) { var v = data[k]; _add(a, k, typeof v === 'function' ? v(ds) : v) }); active.push(typeof b.title === 'function' ? b.title(ds) : (b.title || ''));
  });
  return { a: a, msg: active };
}
function _damage(a, p, pct, talent, ele, basic, mode, dynamic) {
  dynamic = dynamic || {}; var at = _ds(a, p, {}, {}).attr, calc = _value, lv = +p.level || 90, enemyLv = +p.enemyLv || 103;
  if (p.game === 'sr') pct *= 100;
  var atk = calc(a.atk), dmg = 1 + (calc(a.dmg) + (+dynamic.dynamicDmg || 0)) / 100, phy = 1 + (calc(a.phy) + (+dynamic.dynamicPhy || 0)) / 100;
  if (ele === 'phy') dmg = phy;
  var cpct = Math.max(0, Math.min(1, (calc(a.cpct) + (+dynamic.dynamicCpct || 0)) / 100));
  var cdmg = cpct ? (calc(a.cdmg) + (+dynamic.dynamicCdmg || 0)) / 100 : 0;
  var t = a[talent] || {}, plus = +t.plus || 0; pct += +t.pct || 0; dmg += (+t.dmg || 0) / 100; cpct = Math.max(0, Math.min(1, cpct + (+t.cpct || 0) / 100)); cdmg += (+t.cdmg || 0) / 100;
  var def = +a.enemy.def || 0; var ignore = (+a.enemy.ignore || 0) + (+t.ignore || 0); var defNum;
  if (p.game === 'sr') defNum = (200 + lv * 10) / ((200 + lv * 10) + (200 + enemyLv * 10) * (1 - Math.min(1, def / 100 + ignore / 100)));
  else defNum = (lv + 100) / ((lv + 100) + (enemyLv + 100) * (1 - def / 100) * (1 - ignore / 100));
  var k = p.game === 'sr' ? 1 + (+a.kx || 0) / 100 : Math.max(.1, (100 - (10 - (+a.kx || 0))) / 100);
  var base = mode === 'basic' ? basic * (1 + (+a.multi || 0) / 100) + plus : atk * (pct / 100) * (1 + (+a.multi || 0) / 100) + plus;
  if (ele === 'aggravate' || ele === 'spread') { var m = calc(a.mastery); base += (ele === 'aggravate' ? 4.6 : 5) * 361.713 * (1 + 5 * m / (m + 1200)); }
  var enemy = p.game === 'sr' ? 1 + (calc(a.enemydmg) + (+dynamic.dynamicEnemydmg || 0) + (+t.enemydmg || 0)) / 100 : 1;
  var avg = base * dmg * enemy * defNum * k * (1 + cpct * cdmg), crit = base * dmg * enemy * defNum * k * (1 + cdmg);
  return { dmg: crit, avg: avg };
}
function __miao_run(payload) {
  var p = payload, buffed = _buffed(p, (typeof buffs === 'undefined' ? [] : buffs), p.params || {}, p.talent || {}), a = buffed.a, details0 = (typeof details === 'undefined' ? [] : details), out = [];
  details0.forEach(function (raw) {
    var d = typeof raw === 'function' ? raw(_ds(a, p, p.params || {}, p.talent || {})) : raw; if (!d || d.isStatic || (+d.cons && (+p.cons || 0) < +d.cons)) return;
    var params = lodash.merge({}, p.params || {}, typeof d.params === 'function' ? d.params(_ds(a, p, {}, p.talent || {})) : (d.params || {}));
    var b = _buffed(p, (typeof buffs === 'undefined' ? [] : buffs), params, p.talent || {}), ds = _ds(b.a, p, params, p.talent || {}); if (d.check && !d.check(ds)) return;
    var fn = function (pct, talent, ele, basic, mode, dynamic) { return _damage(b.a, p, +pct || 0, talent || false, ele || false, +basic || 0, mode || 'talent', dynamic) };
    fn.basic = function (n, talent, ele, dynamic) { return fn(0, talent, ele, n, 'basic', dynamic) }; fn.dynamic = function (pct, talent, dynamic, ele) { return fn(pct, talent, ele, 0, 'talent', dynamic) }; fn.reaction = function (ele) { return fn(0, ele === 'superBreak' ? 'break' : 'dot', ele, 0, 'basic') };
    var r = d.dmg ? d.dmg(ds, fn) : null; if (r) out.push({ title: typeof d.title === 'function' ? d.title(ds) : d.title, dmg: +r.dmg || 0, avg: +r.avg || 0 });
  });
  return JSON.stringify({ ret: out, msg: buffed.msg, defDmgIdx: typeof defDmgIdx === 'undefined' ? -1 : defDmgIdx, mainAttr: typeof mainAttr === 'undefined' ? 'atk,cpct,cdmg' : mainAttr });
}
'''
