// 交叉验证：用 node 跑原版 miao-plugin 的 Attr.js + ArtisMark.js 链路，
// 与 Python 移植版（core/attr_calc.py / core/artis_mark.py）逐字段 diff。
//
// 做法（同 P3 verify_gacha.mjs）：读出 refs 源码，剥离 import/export，
// 注入最小 stub（lodash/Format/Data/Meta/Base/ArtifactSet/Character）后用 new Function 求值：
// - 加载的真源码：models/attr/AttrData.js、models/attr/Attr.js、models/Weapon.js、
//   models/artis/ArtisAttr.js、models/artis/ArtisMarkCfg.js、models/artis/ArtisMark.js
// - Meta stub 的数据全部从 refs/miao-plugin/resources 加载（extra.js/meta.js/calc.js/
//   artis-mark.js/weapon calc.js/角色 calc.js 与 artis.js 都是真求值）
// - Character/Artis(profile) 为薄 stub，数据来自 tools/attr_cases.json（Python 解析 fixture 的产物）
//
// 用法：node tools/verify_attr.mjs（会先跑 poetry run python tools/verify_attr_py.py）
import fs from 'node:fs'
import path from 'node:path'
import { execSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const REFS = '/Users/liangpuyue/Desktop/develop/refs/miao-plugin'

// Python 侧：生成 attr_cases.json 与 py_attr_out.json
execSync('poetry run python tools/verify_attr_py.py', { cwd: ROOT, stdio: 'inherit' })
const CASES = JSON.parse(fs.readFileSync(path.join(ROOT, 'tools', 'attr_cases.json'), 'utf8'))

// ---------------------------------------------------------------------------
// stub：lodash
// ---------------------------------------------------------------------------
const lodash = {
  forEach (obj, fn) {
    if (Array.isArray(obj)) obj.forEach((v, i) => fn(v, i))
    else Object.keys(obj || {}).forEach((k) => fn(obj[k], k))
  },
  extend (target, ...srcs) { return Object.assign(target, ...srcs) },
  isPlainObject (x) { return x !== null && typeof x === 'object' && !Array.isArray(x) },
  isFunction (x) { return typeof x === 'function' },
  isString (x) { return typeof x === 'string' },
  isEmpty (x) {
    if (x == null) return true
    if (Array.isArray(x) || typeof x === 'string') return x.length === 0
    return Object.keys(x).length === 0
  },
  sortBy (arr, keys) {
    // 真实 lodash 支持字符串 iteratee（sortBy(tmp, 'mark')）
    const ks = Array.isArray(keys) ? keys : [keys]
    return [...arr].sort((a, b) => {
      for (const k of ks) {
        if (a[k] < b[k]) return -1
        if (a[k] > b[k]) return 1
      }
      return 0
    })
  },
  values (obj) { return Object.values(obj) },
  keys (obj) { return Object.keys(obj) },
  mapValues (obj, fn) {
    const ret = {}
    Object.keys(obj || {}).forEach((k) => { ret[k] = typeof fn === 'function' ? fn(obj[k]) : obj[k][fn] })
    return ret
  },
  trim (s) { return String(s).trim() }
}

// ---------------------------------------------------------------------------
// stub：Format（components/Format.js + common/Elem.js 的相关部分）
// ---------------------------------------------------------------------------
const elemAlias = {
  anemo: '风,蒙德', geo: '岩,璃月', electro: '雷,电,雷电,稻妻', dendro: '草,须弥',
  pyro: '火,纳塔', hydro: '水,枫丹', cryo: '冰,至冬'
}
const elemAliasSR = { fire: '火', ice: '冰', wind: '风', elec: '雷', phy: '物理', quantum: '量子', imaginary: '虚数' }
function buildElemMap (alias) {
  const ret = {}
  for (const [key, txt] of Object.entries(alias)) {
    ret[key] = key
    for (const t of txt.split(',')) ret[t] = key
  }
  return ret
}
const ELEM_MAP = { gs: buildElemMap(elemAlias), sr: buildElemMap(elemAliasSR) }
const elemNameOf = (key, game) => {
  const std = ELEM_MAP[game][key] || key
  const txt = (game === 'gs' ? elemAlias : elemAliasSR)[std] || ''
  return game === 'gs' ? txt[0] || '' : txt
}

const Format = {
  pct (num, fix = 1) { return (num * 1).toFixed(fix) + '%' },
  comma (num, fix = 0) { return String(parseFloat((num * 1).toFixed(fix))) },
  percent (num, fix) { return Format.pct(num * 100, fix) },
  isElem (elem = '', game = 'gs') { return !!ELEM_MAP[game][elem] },
  sameElem (k1, k2, game = 'gs') { return ELEM_MAP[game][k1] === ELEM_MAP[game][k2] },
  elem (elem = '', defElem = '', game = 'gs') { return ELEM_MAP[game][String(elem).toLowerCase()] || defElem },
  eachElem (fn, game = 'gs') {
    const alias = game === 'gs' ? elemAlias : elemAliasSR
    Object.keys(alias).forEach((key) => fn(key, game === 'gs' ? alias[key][0] : alias[key]))
  }
}

// ---------------------------------------------------------------------------
// stub：Data / Base
// ---------------------------------------------------------------------------
const Data = {
  readJSON (p) { return JSON.parse(fs.readFileSync(path.join(REFS, p.replace(/^\//, '')), 'utf8')) },
  eachStr (str, fn) { String(str).split(/[,，、]/).forEach((s) => fn(s)) },
  getData (obj, keys) {
    const ret = {}
    for (const part of String(keys).split(',')) {
      const [key, src] = part.split(':')
      ret[key] = src ? obj[src] : obj[key]
    }
    return ret
  }
}

// models/Base.js 的 Proxy getter 行为（AttrData.getAttr 的 this[key] 依赖它）
class Base {
  constructor () {
    return new Proxy(this, {
      get (self, key, receiver) {
        if (key in self) return Reflect.get(self, key, receiver)
        if (self._get) return self._get.call(receiver, key)
        return (self.meta || {})[key]
      }
    })
  }

  get isGs () { return this.game === 'gs' }

  get isSr () { return this.game === 'sr' }

  _getCache (uuid = '') { this._uuid = uuid; return null }

  _cache () { return this }

  getData (keys) { return Data.getData(this, keys) }
}

// ---------------------------------------------------------------------------
// ESM 求值工具
// ---------------------------------------------------------------------------
function stripEsm (code) {
  code = code.replace(/^[ \t]*import[ \t][^\n;]*;?[ \t]*$/gm, '')
  code = code.replace(/^[ \t]*export[ \t]*\{[^}\n]*\}[ \t]*;?[ \t]*$/gm, '')
  code = code.replace(/^[ \t]*export[ \t]*\*[^\n;]*;?[ \t]*$/gm, '')
  code = code.replace(/^[ \t]*export\s+(?=(const|let|var|function|class)\b)/gm, '')
  code = code.replace('export default ', 'const __default__ = ')
  return code
}

function loadModule (file, params, retExpr = '__default__') {
  const code = stripEsm(fs.readFileSync(file, 'utf8'))
  const names = Object.keys(params)
  return new Function(...names, `${code}\nreturn ${retExpr}`)(...names.map((n) => params[n]))
}

// ---------------------------------------------------------------------------
// Meta stub：arti / weapon 的元数据全部真求值 refs resources
// ---------------------------------------------------------------------------
const _artiMetaCache = {}
function artiMeta (game) {
  if (_artiMetaCache[game]) return _artiMetaCache[game]
  const dir = path.join(REFS, 'resources', `meta-${game}`, 'artifact')
  let ret
  if (game === 'gs') {
    const extra = loadModule(path.join(dir, 'extra.js'), { lodash, Format },
      '{ mainAttr, subAttr, attrMap, attrNameMap, mainIdMap, attrIdMap }')
    ret = {
      ...extra,
      artiBuffs: loadModule(path.join(dir, 'calc.js'), {}),
      usefulAttr: loadModule(path.join(dir, 'artis-mark.js'), {}, '{ usefulAttr }').usefulAttr
    }
  } else {
    const meta = loadModule(path.join(dir, 'meta.js'), { lodash, Format }, '{ mainAttr, subAttr, attrMap }')
    ret = {
      ...meta,
      metaData: JSON.parse(fs.readFileSync(path.join(dir, 'meta.json'), 'utf8')),
      artiBuffs: loadModule(path.join(dir, 'calc.js'), {}),
      usefulAttr: loadModule(path.join(dir, 'artis-mark.js'), {}, '{ usefulAttr }').usefulAttr
    }
  }
  _artiMetaCache[game] = ret
  return ret
}

const _weaponBuffsCache = {}
function weaponBuffs (game) {
  if (_weaponBuffsCache[game]) return _weaponBuffsCache[game]
  const dir = path.join(REFS, 'resources', `meta-${game}`, 'weapon')
  let buffs = {}
  if (game === 'gs') {
    // resources/meta-gs/weapon/index.js 的 step/attr stub
    const step = function (start, _step) {
      if (!_step) _step = start / 4
      const ret = []
      for (let idx = 0; idx <= 5; idx++) ret.push(start + _step * idx)
      return ret
    }
    const attr = function (key, start, _step) {
      const refine = {}
      refine[key] = step(start, _step)
      return { title: `${key}提高[key]`, isStatic: true, refine }
    }
    for (const type of ['polearm', 'catalyst', 'sword', 'bow', 'claymore']) {
      const f = path.join(dir, type, 'calc.js')
      if (!fs.existsSync(f)) continue
      const ret = loadModule(f, {})(step, attr)
      buffs = lodash.extend(buffs, ret)
    }
  } else {
    // resources/meta-sr/weapon/index.js 的 staticIdx/keyIdx stub
    const staticIdx = (idx, key) => ({ isStatic: true, idx, key })
    const keyIdx = (title, key, idx) => {
      if (lodash.isPlainObject(key)) {
        return (tables) => {
          const data = {}
          lodash.forEach(key, (i, k) => { data[k] = tables[i] })
          return { title, data }
        }
      }
      return { title, idx, key }
    }
    for (const type of '存护,丰饶,毁灭,记忆,同谐,虚无,巡猎,智识,欢愉'.split(',')) {
      const f = path.join(dir, type, 'calc.js')
      if (!fs.existsSync(f)) continue
      const ret = loadModule(f, {})(staticIdx, keyIdx)
      buffs = lodash.extend(buffs, ret)
    }
  }
  _weaponBuffsCache[game] = buffs
  return buffs
}

const _weaponIndexCache = {}
function weaponIndex (game) {
  if (_weaponIndexCache[game]) return _weaponIndexCache[game]
  const dir = path.join(REFS, 'resources', `meta-${game}`, 'weapon')
  const map = new Map()
  for (const typeDir of fs.readdirSync(dir)) {
    const typePath = path.join(dir, typeDir)
    if (!fs.statSync(typePath).isDirectory()) continue
    for (const sub of fs.readdirSync(typePath)) {
      const f = path.join(typePath, sub, 'data.json')
      if (!fs.existsSync(f)) continue
      const data = JSON.parse(fs.readFileSync(f, 'utf8'))
      const entry = { id: data.id, name: data.name || sub, type: typeDir, star: data.star }
      map.set(entry.name, entry)
      if (!map.has(sub)) map.set(sub, entry)
      if (data.id != null && !map.has(String(data.id))) map.set(String(data.id), entry)
    }
  }
  _weaponIndexCache[game] = map
  return map
}

const Meta = {
  getMeta (game, type) {
    if (type === 'arti') return artiMeta(game)
    if (type === 'weapon') return { weaponBuffs: weaponBuffs(game), descFix: {} }
    return {}
  },
  getData (game, type, name) {
    if (type === 'weapon') return weaponIndex(game).get(String(name)) || null
    return null
  }
}

// ArtifactSet.getArtisSetBuff 是纯静态方法，直接 1:1 移植
const ArtifactSet = {
  getArtisSetBuff (name, num, game = 'gs') {
    const { artiBuffs } = Meta.getMeta(game, 'arti')
    const ret = (artiBuffs[name] && artiBuffs[name][num]) || artiBuffs[name + num]
    if (!ret) return false
    if (lodash.isPlainObject(ret)) return [ret]
    return ret
  }
}

// ---------------------------------------------------------------------------
// 加载真源码模块
// ---------------------------------------------------------------------------
const M = (p) => path.join(REFS, 'models', p)
const AttrData = loadModule(M('attr/AttrData.js'), { lodash, Base, Format })
const Weapon = loadModule(M('Weapon.js'), { Base, Data, Format, Meta, lodash })
const ArtisAttr = loadModule(M('artis/ArtisAttr.js'), { Format, Meta, lodash })
// ArtisMark 与 ArtisMarkCfg 循环引用：用可变 ref 后填
const markCfgRef = {}
const ArtisMark = loadModule(M('artis/ArtisMark.js'), { lodash, Data, Format, Meta, ArtisMarkCfg: markCfgRef, Artifact: {} })
const ArtisMarkCfg = loadModule(M('artis/ArtisMarkCfg.js'), { lodash, ArtisMark, Meta })
markCfgRef.getCfg = ArtisMarkCfg.getCfg
const Attr = loadModule(M('attr/Attr.js'), { Base, Format, Meta, Weapon, ArtifactSet, AttrData, lodash })

// ---------------------------------------------------------------------------
// Character / Artis(profile) 薄 stub
// ---------------------------------------------------------------------------
const _charDataCache = {}
function charData (game, avatar) {
  const key = `${game}:${avatar.id}`
  if (_charDataCache[key]) return _charDataCache[key]
  const dir = path.join(REFS, 'resources', `meta-${game}`, 'character')
  for (const sub of fs.readdirSync(dir)) {
    const f = path.join(dir, sub, 'data.json')
    if (!fs.existsSync(f)) continue
    const data = JSON.parse(fs.readFileSync(f, 'utf8'))
    if (String(data.id) === String(avatar.id) || data.name === avatar.name || sub === avatar.name) {
      _charDataCache[key] = data
      return data
    }
  }
  throw new Error(`char not found: ${game} ${avatar.id} ${avatar.name}`)
}

// 角色 calc.js → CharCfg.getCalcRule 的 {buffs}（Attr.setCharAttr 只用 buffs）
const _charCalcCache = {}
function charCalcRule (game, name) {
  const key = `${game}:${name}`
  if (key in _charCalcCache) return _charCalcCache[key]
  const f = path.join(REFS, 'resources', `meta-${game}`, 'character', name, 'calc.js')
  let ret = false
  if (fs.existsSync(f)) {
    try {
      const cfg = loadModule(f, { lodash, Format }, '{ buffs: (typeof buffs === "undefined" ? [] : buffs) }')
      if (cfg.buffs && cfg.buffs.length > 0) ret = { buffs: cfg.buffs }
    } catch (e) {
      console.log(`warn: ${name}/calc.js eval 失败，按无静态 buff 处理: ${e.message}`)
    }
  }
  _charCalcCache[key] = ret
  return ret
}

// 角色 artis.js → CharCfg.getArtisCfg（export default function）
const _charArtisCache = {}
function charArtisCfg (game, name) {
  const key = `${game}:${name}`
  if (key in _charArtisCache) return _charArtisCache[key]
  const f = path.join(REFS, 'resources', `meta-${game}`, 'character', name, 'artis.js')
  let ret = false
  if (fs.existsSync(f)) ret = loadModule(f, { lodash, Format })
  _charArtisCache[key] = ret
  return ret
}

function makeChar (avatar, game) {
  const data = charData(game, avatar)
  const char = {
    id: data.id,
    name: data.name,
    abbr: data.abbr || data.name,
    game,
    isGs: game === 'gs',
    isSr: game === 'sr',
    elem: avatar.elem || data.elem || '',
    weapon: data.weapon,
    baseAttr: data.baseAttr,
    detail: data,
    isElem (e = '') {
      e = String(e).toLowerCase()
      return char.elem === e || elemNameOf(char.elem, game) === e
    },
    // Character.getLvAttr 星铁分支 1:1 移植
    getLvAttr (level, promote) {
      const metaAttr = char.detail?.attr
      if (!metaAttr) return false
      const lvAttr = metaAttr[promote]
      const ret = {}
      lodash.forEach(lvAttr.attrs, (v, k) => { ret[k] = v * 1 })
      lodash.forEach(lvAttr.grow, (v, k) => { ret[k] = ret[k] * 1 + v * (level - 1) })
      return ret
    },
    getCalcRule () { return charCalcRule(game, data.name) },
    getArtisCfg () { return charArtisCfg(game, data.name) }
  }
  return char
}

// 套装简称（ArtisSet.getSetData 的 abbrs 用）
const _setAbbrCache = {}
function setAbbr (game) {
  if (_setAbbrCache[game]) return _setAbbrCache[game]
  const f = path.join(REFS, 'resources', `meta-${game}`, 'artifact', 'alias.js')
  const ret = loadModule(f, {}, '{ setAbbr: (typeof setAbbr === "undefined" ? {} : setAbbr), artiSetAbbr: (typeof artiSetAbbr === "undefined" ? {} : artiSetAbbr) }')
  _setAbbrCache[game] = ret.setAbbr || ret.artiSetAbbr || {}
  return _setAbbrCache[game]
}

// 圣遗物散件索引（Artifact.get / ArtifactSet.get 的解析部分）
const _gsSetByPrefix = {}
{
  const data = JSON.parse(fs.readFileSync(path.join(REFS, 'resources/meta-gs/artifact/data.json'), 'utf8'))
  for (const s of Object.values(data)) {
    const pieces = {}
    let prefix = ''
    for (const [idx, piece] of Object.entries(s.idxs || {})) {
      const pid = String(piece.id || '')
      prefix = prefix || pid.slice(0, 2)
      pieces[Number(idx)] = piece.name
    }
    if (prefix) _gsSetByPrefix[prefix] = { name: s.name, pieces }
  }
}
const _srArtiByTid = {}
{
  const data = JSON.parse(fs.readFileSync(path.join(REFS, 'resources/meta-sr/artifact/data.json'), 'utf8'))
  for (const s of Object.values(data)) {
    for (const piece of Object.values(s.idxs || {})) {
      for (const [tid, star] of Object.entries(piece.ids || {})) {
        _srArtiByTid[String(tid)] = { set: s.name, name: piece.name, star: Number(star) }
      }
    }
  }
}

// Artis.setArtisData + setArtis：散件名/套装解析 + ArtisAttr.getData 词条换算
function buildPieces (avatar, game) {
  const pieces = {}
  for (const [idxStr, ds] of Object.entries(avatar.artis || {})) {
    const idx = Number(idxStr)
    let name, set, star
    if (game === 'gs') {
      const idStr = String(ds.id || '')
      if (!/^\d{5}$/.test(idStr)) continue
      const setData = _gsSetByPrefix[idStr.slice(0, 2)]
      if (!setData) continue
      const pos = [4, 2, 5, 1, 3][Number(idStr[3]) - 1]
      name = setData.pieces[pos]
      set = setData.name
      star = ds.star || 5
    } else {
      const hit = _srArtiByTid[String(ds.id || '')]
      if (!hit) continue
      name = hit.name
      set = hit.set
      star = hit.star
    }
    const attr = ArtisAttr.getData(
      { mainId: ds.mainId, attrIds: ds.attrIds || [], level: ds.level || 0, star }, idx, game
    )
    pieces[idx] = { name, set, level: ds.level || 0, star, main: attr && attr.main, attrs: (attr && attr.attrs) || [] }
  }
  return pieces
}

// Artis 类的 forEach / eachArtisSet / getSetData / is（Attr 与 ArtisMark 用到的子集）
function makeArtis (pieces, game) {
  const artis = { ...pieces }
  return {
    game,
    isGs: game === 'gs',
    mark: 0,
    markClass: '',
    forEach (fn) { lodash.forEach(artis, (ds, idx) => { if (ds.name) fn(ds, idx) }) },
    getSetData () {
      const setCount = {}
      this.forEach((a) => { setCount[a.set] = (setCount[a.set] || 0) + 1 })
      const sets = {}
      const names = []
      const abbrs = []
      const abbrMap = setAbbr(game)
      for (const s of Object.keys(setCount)) {
        if (setCount[s] >= 2) {
          const count = setCount[s] >= 4 ? 4 : 2
          sets[s] = count
          names.push(s)
          abbrs.push((abbrMap[s] || s) + count, s + count)
        }
      }
      return { sets, names, imgs: [], abbrs }
    },
    // ArtifactSet.eachSet：>=4 件先回调 2 件套再回调实际件数
    eachArtisSet (fn) {
      lodash.forEach(this.getSetData().sets, (v, k) => {
        if (v >= 4) fn({ name: k }, 2)
        fn({ name: k }, v)
      })
    },
    is (check) {
      let ret = false
      const abbrs = this.getSetData().abbrs
      Data.eachStr(check, (s) => { if (abbrs.includes(s)) ret = true })
      return ret
    }
  }
}

// ---------------------------------------------------------------------------
// 跑用例
// ---------------------------------------------------------------------------
const ATTR_KEYS = {
  gs: ['hp', 'atk', 'def', 'mastery', 'cpct', 'cdmg', 'recharge', 'dmg', 'phy', 'heal', 'shield',
    'coloringDmg', 'hpBase', 'atkBase', 'defBase'],
  sr: ['hp', 'atk', 'def', 'speed', 'cpct', 'cdmg', 'recharge', 'dmg', 'heal', 'stance',
    'effPct', 'effDef', 'joy', 'hpBase', 'atkBase', 'defBase', 'speedBase']
}

const jsOut = {}
for (const [label, { game, avatar }] of Object.entries(CASES)) {
  const char = makeChar(avatar, game)
  const artis = makeArtis(buildPieces(avatar, game), game)
  const profile = {
    game,
    isGs: game === 'gs',
    isSr: game === 'sr',
    isProfile: true,
    id: avatar.id,
    elem: avatar.elem || char.elem,
    cons: avatar.cons || 0,
    level: avatar.level,
    promote: avatar.promote,
    trees: avatar.trees || [],
    char,
    weapon: { ...(avatar.weapon || {}), affix: (avatar.weapon || {}).affix || 1 },
    artis
  }
  const attr = Attr.create(profile).calc()
  profile.attr = attr
  const mark = ArtisMark.getMarkDetail(profile, false)
  const cfg = ArtisMarkCfg.getCfg(profile)
  if (process.env.DEBUG_DUMP) {
    fs.writeFileSync(path.join(ROOT, 'tools', `debug_js_${label}.json`), JSON.stringify({
      posMaxMark: cfg.posMaxMark,
      pieces: profile.artis,
      attrs: lodash.mapValues(cfg.attrs, (ds) => ({ weight: ds.weight, fixWeight: ds.fixWeight, mark: ds.mark }))
    }, null, 1))
  }
  jsOut[label] = {
    attr: Object.fromEntries(ATTR_KEYS[game].map((k) => [k, attr[k] ?? 0])),
    mark: {
      mark: mark._mark,
      markClass: mark.markClass ?? null,
      classTitle: mark.classTitle,
      charWeight: lodash.mapValues(cfg.attrs, (ds) => ds.weight),
      artis: Object.fromEntries(Object.entries(mark.artis).map(([i, a]) => [i, { mark: a._mark, markClass: a.markClass ?? null }]))
    }
  }
}
fs.writeFileSync(path.join(ROOT, 'tools', 'js_attr_out.json'), JSON.stringify(jsOut, null, 1))

// ---------------------------------------------------------------------------
// 与 Python 结果 diff：属性允许 <0.1% 浮点误差，评分要求完全一致
// ---------------------------------------------------------------------------
const pyOut = JSON.parse(fs.readFileSync(path.join(ROOT, 'tools', 'py_attr_out.json'), 'utf8'))

function close (a, b, rel = 0.001) {
  if (a === b) return true
  const d = Math.abs((a ?? 0) - (b ?? 0))
  return d <= Math.max(1e-6, Math.abs(b ?? 0) * rel)
}

let fail = 0
for (const label of Object.keys(jsOut)) {
  const errors = []
  const [j, p] = [jsOut[label], pyOut[label]]
  for (const k of Object.keys(j.attr)) {
    if (!close(j.attr[k], p.attr[k])) errors.push(`attr.${k}: js=${j.attr[k]} py=${p.attr[k]}`)
  }
  // 评分：mark 值要求高精度一致（1e-9 相对误差），档位/标题/权重完全一致
  if (!close(j.mark.mark, p.mark.mark, 1e-9)) errors.push(`mark: js=${j.mark.mark} py=${p.mark.mark}`)
  if (j.mark.markClass !== p.mark.markClass) errors.push(`markClass: js=${j.mark.markClass} py=${p.mark.markClass}`)
  if (j.mark.classTitle !== p.mark.classTitle) errors.push(`classTitle: js=${j.mark.classTitle} py=${p.mark.classTitle}`)
  if (JSON.stringify(j.mark.charWeight) !== JSON.stringify(p.mark.charWeight)) {
    errors.push(`charWeight: js=${JSON.stringify(j.mark.charWeight)} py=${JSON.stringify(p.mark.charWeight)}`)
  }
  for (const [i, a] of Object.entries(j.mark.artis)) {
    const pa = (p.mark.artis || {})[i] || {}
    if (!close(a.mark, pa.mark, 1e-9)) errors.push(`artis[${i}].mark: js=${a.mark} py=${pa.mark}`)
    if (a.markClass !== pa.markClass) errors.push(`artis[${i}].markClass: js=${a.markClass} py=${pa.markClass}`)
  }
  if (errors.length === 0) {
    console.log(`OK   ${label}`)
  } else {
    fail++
    console.log(`DIFF ${label}`)
    for (const e of errors) console.log(`     ${e}`)
  }
}
console.log(fail === 0 ? `\n全部 ${Object.keys(jsOut).length} 个用例一致` : `\n${fail} 个用例不一致`)
process.exit(fail === 0 ? 0 : 1)
