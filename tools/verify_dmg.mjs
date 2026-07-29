// 交叉验证：用 node 跑原版 miao-plugin 的伤害计算全链路，
// 与 Python 移植版（dmg/service.py + dmg/js_runtime.py）逐段 diff。
//
// 加载的真源码（stub 掉 Yunzai 依赖，手法同 verify_attr.mjs）：
// - 面板：models/attr/AttrData.js、models/attr/Attr.js、models/Weapon.js、
//   models/artis/ArtisAttr.js（面板必须与原版图完全一致，否则伤害无从比起）
// - 伤害：models/dmg/{AttrItem,DmgCalcMeta,DmgMastery,DmgAttr,DmgBuffs,DmgCalc}.js
//   + models/ProfileDmg.js（编排：角色 calc.js + 武器 calc + 圣遗物套装 calc）
// - stub 掉的依赖：lodash / Format(+Elem) / Data / Base / Meta / ArtifactSet /
//   Character（ProfileDmg 用，薄 stub）/ Common.cfg / miaoPath / MiaoError
// - 角色 calc.js 由 ProfileDmg.getCalcRule 通过真实 dynamic import 加载（miaoPath 指向 refs）
//
// 输入：tools/attr_cases.json（与 verify_attr 相同的三例：优菈/迪卢克/镜流Pro）
// 输出：tools/js_dmg_out.json，并与 tools/py_dmg_out.json（verify_dmg_py.py 产物）对比。
//
// 用法：node tools/verify_dmg.mjs（会先跑 poetry run python tools/verify_dmg_py.py）
import fs from 'node:fs'
import path from 'node:path'
import { execSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const REFS = '/Users/liangpuyue/Desktop/develop/refs/miao-plugin'

// Python 侧：生成 attr_cases.json 与 py_dmg_out.json
execSync('poetry run python tools/verify_dmg_py.py', { cwd: ROOT, stdio: 'inherit' })
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
  concat (...args) { return [].concat(...args) },
  filter (arr, fn) { return (arr || []).filter(fn) },
  isPlainObject (x) { return x !== null && typeof x === 'object' && !Array.isArray(x) },
  isFunction (x) { return typeof x === 'function' },
  isString (x) { return typeof x === 'string' },
  isNumber (x) { return typeof x === 'number' },
  isUndefined (x) { return typeof x === 'undefined' },
  isEmpty (x) {
    if (x == null) return true
    if (Array.isArray(x) || typeof x === 'string') return x.length === 0
    return Object.keys(x).length === 0
  },
  sortBy (arr, keys) {
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
  merge (target, ...srcs) {
    target = target || {}
    for (const src of srcs) {
      if (!src) continue
      for (const [k, v] of Object.entries(src)) {
        if (lodash.isPlainObject(v) && lodash.isPlainObject(target[k])) lodash.merge(target[k], v)
        else if (lodash.isPlainObject(v)) target[k] = lodash.merge({}, v)
        else target[k] = v
      }
    }
    return target
  },
  trim (s) { return String(s).trim() }
}

// ---------------------------------------------------------------------------
// stub：Format + Elem（components/Format.js + components/common/Elem.js）
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
// Elem.elem / Elem.elemName：只用 gs 映射（原版如此，sr 元素名经 gs 别名表恰好命中）
const elemGsOnly = (elem = '', defElem = '') => ELEM_MAP.gs[String(elem).toLowerCase()] || defElem

const Format = {
  pct (num, fix = 1) { return (num * 1).toFixed(fix) + '%' },
  comma (num, fix = 0) { return String(parseFloat((num * 1).toFixed(fix))) },
  percent (num, fix) { return Format.pct(num * 100, fix) },
  isElem (elem = '', game = 'gs') { return !!ELEM_MAP[game][elem] },
  sameElem (k1, k2, game = 'gs') { return ELEM_MAP[game][k1] === ELEM_MAP[game][k2] },
  elem (elem = '', defElem = '', game = 'gs') { return ELEM_MAP[game][String(elem).toLowerCase()] || defElem },
  // 原版 Elem.elemName 只查 gs 的 elemTitleMap（sr 元素靠别名恰好命中，量子/虚数会得到 ''）
  elemName (elem = '', defName = '') {
    const key = elemGsOnly(elem)
    return key ? (elemAlias[key] || '')[0] : defName
  },
  eachElem (fn, game = 'gs') {
    const alias = game === 'gs' ? elemAlias : elemAliasSR
    Object.keys(alias).forEach((key) => fn(key, game === 'gs' ? alias[key][0] : alias[key]))
  }
}

// ---------------------------------------------------------------------------
// stub：Data / Base / Common / MiaoError / miaoPath
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

class MiaoError extends Error {}
const Common = { cfg: () => false }
const miaoPath = REFS

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
// Meta stub（同 verify_attr.mjs）：arti / weapon 元数据全部真求值 refs resources
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

const ArtifactSet = {
  getArtisSetBuff (name, num, game = 'gs') {
    const { artiBuffs } = Meta.getMeta(game, 'arti')
    const ret = (artiBuffs[name] && artiBuffs[name][num]) || artiBuffs[name + num]
    if (!ret) return false
    if (lodash.isPlainObject(ret)) return [ret]
    return ret
  },
  // ArtifactSet.eachSet：>=4 件先回调 2 件套再回调实际件数；get 返回薄 stub
  eachSet (idxs, fn, game = 'gs') {
    lodash.forEach(idxs || {}, (v, k) => {
      if (v >= 4) fn({ name: k }, 2)
      fn({ name: k }, v)
    })
  },
  get (name) { return { name } }
}

// ---------------------------------------------------------------------------
// 加载真源码模块：面板 + 伤害引擎
// ---------------------------------------------------------------------------
const M = (p) => path.join(REFS, 'models', p)
const AttrData = loadModule(M('attr/AttrData.js'), { lodash, Base, Format })
const Weapon = loadModule(M('Weapon.js'), { Base, Data, Format, Meta, lodash })
const ArtisAttr = loadModule(M('artis/ArtisAttr.js'), { Format, Meta, lodash })
const Attr = loadModule(M('attr/Attr.js'), { Base, Format, Meta, Weapon, ArtifactSet, AttrData, lodash })

const AttrItem = loadModule(M('dmg/AttrItem.js'), {})
const DmgCalcMeta = loadModule(M('dmg/DmgCalcMeta.js'), { lodash },
  '{ erType, erTitle, eleBaseDmg, cryBaseDmg, breakBaseDmg, elationBaseDmg }')
const DmgMastery = loadModule(M('dmg/DmgMastery.js'), { erType: DmgCalcMeta.erType })
const DmgAttr = loadModule(M('dmg/DmgAttr.js'), {
  eleBaseDmg: DmgCalcMeta.eleBaseDmg, lodash, DmgMastery, Format, Meta, AttrItem
})
const DmgBuffs = loadModule(M('dmg/DmgBuffs.js'), { lodash, ArtifactSet, Weapon })
const DmgCalc = loadModule(M('dmg/DmgCalc.js'), {
  eleBaseDmg: DmgCalcMeta.eleBaseDmg,
  erTitle: DmgCalcMeta.erTitle,
  breakBaseDmg: DmgCalcMeta.breakBaseDmg,
  cryBaseDmg: DmgCalcMeta.cryBaseDmg,
  elationBaseDmg: DmgCalcMeta.elationBaseDmg,
  DmgMastery,
  lodash
})

// ---------------------------------------------------------------------------
// Character / Artis(profile) 薄 stub（同 verify_attr.mjs，供面板 Attr 用）
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

const GS_WEAPON_TYPE_NAME = { sword: '单手剑', catalyst: '法器', bow: '弓', claymore: '双手剑', polearm: '长柄武器' }

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
    sp: data.sp,
    weaponTypeName: game === 'sr' ? data.weapon : (GS_WEAPON_TYPE_NAME[String(data.weapon || '').toLowerCase()] || ''),
    isElem (e = '') {
      e = String(e).toLowerCase()
      return char.elem === e || elemNameOf(char.elem, game) === e
    },
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

// ProfileDmg 的 Character.get({id, elem}) stub：按当前用例返回
const CharacterStub = { current: null }
const Character = { get () { return CharacterStub.current } }

const _setAbbrCache = {}
function setAbbr (game) {
  if (_setAbbrCache[game]) return _setAbbrCache[game]
  const f = path.join(REFS, 'resources', `meta-${game}`, 'artifact', 'alias.js')
  const ret = loadModule(f, {}, '{ setAbbr: (typeof setAbbr === "undefined" ? {} : setAbbr), artiSetAbbr: (typeof artiSetAbbr === "undefined" ? {} : artiSetAbbr) }')
  _setAbbrCache[game] = ret.setAbbr || ret.artiSetAbbr || {}
  return _setAbbrCache[game]
}

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

function makeArtis (pieces, game) {
  const artis = { ...pieces }
  return {
    game,
    isGs: game === 'gs',
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
// 加载 ProfileDmg（原版编排）
// ---------------------------------------------------------------------------
const ProfileDmg = loadModule(M('ProfileDmg.js'), {
  fs, lodash, Base, Character, DmgBuffs, DmgAttr, DmgCalc, MiaoError, Meta, Common, miaoPath
})

// ---------------------------------------------------------------------------
// 跑用例：构造 Avatar.calcDmg 的 ds，调用 calcData(mode: 'dmg')
// ---------------------------------------------------------------------------
const enemyLvOf = (game) => (game === 'gs' ? 103 : 80)

function rowOf (ds) {
  return { title: ds.title, dmg: ds.dmg ?? null, avg: ds.avg ?? null }
}

async function runCase (label, game, avatar) {
  const char = makeChar(avatar, game)
  const artis = makeArtis(buildPieces(avatar, game), game)
  const attrProfile = {
    game,
    isGs: game === 'gs',
    isSr: game === 'sr',
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
  const attr = Attr.create(attrProfile).calc()

  // Avatar.calcDmg：ds = getData('id,level,elem,attr,cons,artis:artis.sets,trees,uid') + talent + weapon
  const ds = {
    id: avatar.id,
    level: avatar.level,
    elem: avatar.elem || char.elem,
    attr,
    cons: avatar.cons || 0,
    artis: artis.getSetData().sets,
    trees: avatar.trees || [],
    uid: '800055548',
    talent: avatar.talent || {},
    weapon: { name: (avatar.weapon || {}).name, affix: (avatar.weapon || {}).affix || 1, level: (avatar.weapon || {}).level }
  }
  CharacterStub.current = char
  const dmg = new ProfileDmg(ds, game)
  const calc = await dmg.calcData({ enemyLv: enemyLvOf(game), mode: 'dmg' })
  if (!calc) throw new Error(`${label}: calcData 返回 false`)

  const out = {
    rows: calc.ret.map(rowOf),
    selectedIdx: calc.dmgCfg?.userIdx ?? null,
    selected: calc.dmgCfg?.basicRet ? rowOf(calc.dmgCfg.basicRet) : null,
    msg: calc.msg || [],
    enemyName: calc.enemyName,
    dmgRet: (calc.dmgRet || []).map((row) => row.map((cell) => (cell.type === 'na' ? { type: 'na' } : { type: cell.type, dmg: cell.dmg, avg: cell.avg }))),
    byIdx: {}
  }
  // 手动序号（#角色伤害N）：校验 idxIsInput 路径
  for (const idx of [1, 2]) {
    if (idx > out.rows.length) continue
    CharacterStub.current = char
    const dmg2 = new ProfileDmg(ds, game)
    const c2 = await dmg2.calcData({ enemyLv: enemyLvOf(game), mode: 'dmg', dmgIdx: idx, idxIsInput: true })
    out.byIdx[String(idx)] = c2.dmgCfg?.basicRet ? rowOf(c2.dmgCfg.basicRet) : null
  }
  // 越界序号：原版抛 MiaoError('序号输入错误')
  try {
    CharacterStub.current = char
    const dmg3 = new ProfileDmg(ds, game)
    await dmg3.calcData({ enemyLv: enemyLvOf(game), mode: 'dmg', dmgIdx: out.rows.length + 1, idxIsInput: true })
    out.idxOverflowError = null
  } catch (e) {
    out.idxOverflowError = e.message
  }
  return out
}

const jsOut = {}
for (const [label, { game, avatar }] of Object.entries(CASES)) {
  jsOut[label] = await runCase(label, game, avatar)
}
fs.writeFileSync(path.join(ROOT, 'tools', 'js_dmg_out.json'), JSON.stringify(jsOut, null, 1))

// ---------------------------------------------------------------------------
// 与 Python 结果 diff：标题逐行对齐，dmg/avg 相对误差 <0.5%
// ---------------------------------------------------------------------------
const pyOut = JSON.parse(fs.readFileSync(path.join(ROOT, 'tools', 'py_dmg_out.json'), 'utf8'))

const REL = 0.005
function close (a, b, rel = REL) {
  if (a === b) return true
  if (a == null || b == null) return a == null && b == null
  const d = Math.abs(a - b)
  return d <= Math.max(1e-6, Math.abs(b) * rel)
}

let fail = 0
for (const label of Object.keys(jsOut)) {
  const errors = []
  const [j, p] = [jsOut[label], pyOut[label] || {}]
  const jRows = j.rows || []
  const pRows = p.rows || []
  if (jRows.length !== pRows.length) errors.push(`rows.length: js=${jRows.length} py=${pRows.length}`)
  for (let i = 0; i < Math.min(jRows.length, pRows.length); i++) {
    const [jr, pr] = [jRows[i], pRows[i]]
    if (jr.title !== pr.title) {
      errors.push(`rows[${i}].title: js=${JSON.stringify(jr.title)} py=${JSON.stringify(pr.title)}`)
      continue
    }
    if (!close(jr.dmg, pr.dmg)) errors.push(`rows[${i}](${jr.title}).dmg: js=${jr.dmg} py=${pr.dmg}`)
    if (!close(jr.avg, pr.avg)) errors.push(`rows[${i}](${jr.title}).avg: js=${jr.avg} py=${pr.avg}`)
  }
  if (j.selectedIdx !== p.selectedIdx) errors.push(`selectedIdx: js=${j.selectedIdx} py=${p.selectedIdx}`)
  if (j.selected && p.selected) {
    if (j.selected.title !== p.selected.title) errors.push(`selected.title: js=${j.selected.title} py=${p.selected.title}`)
    if (!close(j.selected.dmg, p.selected.dmg)) errors.push(`selected.dmg: js=${j.selected.dmg} py=${p.selected.dmg}`)
    if (!close(j.selected.avg, p.selected.avg)) errors.push(`selected.avg: js=${j.selected.avg} py=${p.selected.avg}`)
  } else if (j.selected || p.selected) {
    errors.push(`selected: js=${JSON.stringify(j.selected)} py=${JSON.stringify(p.selected)}`)
  }
  if (JSON.stringify(j.msg) !== JSON.stringify(p.msg || [])) {
    errors.push(`msg: js=${JSON.stringify(j.msg)} py=${JSON.stringify(p.msg)}`)
  }
  for (const [idx, jr] of Object.entries(j.byIdx || {})) {
    const pr = (p.byIdx || {})[idx]
    if (!pr) { errors.push(`byIdx[${idx}]: missing in py`); continue }
    if (jr.title !== pr.title) errors.push(`byIdx[${idx}].title: js=${jr.title} py=${pr.title}`)
    if (!close(jr.dmg, pr.dmg)) errors.push(`byIdx[${idx}].dmg: js=${jr.dmg} py=${pr.dmg}`)
    if (!close(jr.avg, pr.avg)) errors.push(`byIdx[${idx}].avg: js=${jr.avg} py=${pr.avg}`)
  }
  if ((j.idxOverflowError || null) !== (p.idxOverflowError || null)) {
    errors.push(`idxOverflowError: js=${j.idxOverflowError} py=${p.idxOverflowError}`)
  }
  // dmgRet 属性增减矩阵（mode 'dmg'）
  const jd = j.dmgRet || []
  const pd = p.dmgRet || []
  if (jd.length !== pd.length) errors.push(`dmgRet.length: js=${jd.length} py=${pd.length}`)
  for (let r = 0; r < Math.min(jd.length, pd.length); r++) {
    if (jd[r].length !== (pd[r] || []).length) errors.push(`dmgRet[${r}].length: js=${jd[r].length} py=${(pd[r] || []).length}`)
    for (let c = 0; c < Math.min(jd[r].length, (pd[r] || []).length); c++) {
      const [jc, pc] = [jd[r][c], pd[r][c]]
      if (jc.type !== pc.type) { errors.push(`dmgRet[${r}][${c}].type: js=${jc.type} py=${pc.type}`); continue }
      if (jc.type === 'na') continue
      if (!close(jc.avg, pc.avg)) errors.push(`dmgRet[${r}][${c}].avg: js=${jc.avg} py=${pc.avg}`)
      if (!close(jc.dmg, pc.dmg)) errors.push(`dmgRet[${r}][${c}].dmg: js=${jc.dmg} py=${pc.dmg}`)
    }
  }
  if (errors.length === 0) {
    console.log(`OK   ${label}（${jRows.length} 段，selected=${j.selectedIdx}）`)
  } else {
    fail++
    console.log(`DIFF ${label}`)
    for (const e of errors) console.log(`     ${e}`)
  }
}
console.log(fail === 0 ? `\n全部 ${Object.keys(jsOut).length} 个用例一致（误差 <0.5%）` : `\n${fail} 个用例不一致`)
process.exit(fail === 0 ? 0 : 1)
