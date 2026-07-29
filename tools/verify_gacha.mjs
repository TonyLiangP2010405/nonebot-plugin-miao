// 交叉验证：用 node 跑原版 miao-plugin GachaData.js（analyse/stat），与 Python 移植版逐字段 diff
//
// 做法：读出 GachaData.js 源码，剥离 import/export，注入最小 stub 后用 new Function 求值：
// - lodash：仅实现 GachaData 用到的 forEach/filter/values/sortBy/extend/isEmpty
// - moment：仅支持本文件用到的 'MM-DD' / 'YY-MM-DD' 格式
// - Character/Weapon：直接扫描 refs/miao-plugin/resources 的 data.json 建索引
//   （fixture 只用规范名，不做别名匹配；img 路径规则与 nonebot_plugin_miao/core/meta.py 一致）
// - Data.readJSON：映射到 tools/fixtures 下的记录文件（与 Python 版读同一份 fixture）
// - poolDetail/mixPoolDetail/poolDetailSr：eval refs 里的原始 pool 数据文件
//
// 用法：node tools/verify_gacha.mjs（会先跑 poetry run python tools/verify_py.py 生成 py_out.json）
import fs from 'node:fs'
import path from 'node:path'
import { execSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const REFS = '/Users/liangpuyue/Desktop/develop/refs/miao-plugin'
const FIXTURES = path.join(ROOT, 'tools', 'fixtures')

// ---------------------------------------------------------------------------
// stub：lodash（只实现 GachaData.js 用到的函数）
// ---------------------------------------------------------------------------
const lodash = {
  forEach (obj, fn) {
    if (Array.isArray(obj)) {
      obj.forEach((v, i) => fn(v, i))
    } else {
      // Object.keys 对整数形态 key 自动按数值升序，与真实 lodash 行为一致
      Object.keys(obj || {}).forEach((k) => fn(obj[k], k))
    }
  },
  filter (arr, fn) {
    return arr.filter(fn)
  },
  values (obj) {
    return Object.values(obj)
  },
  sortBy (arr, keys) {
    // V8 的 Array.sort 是稳定排序，与 lodash.sortBy 一致
    return [...arr].sort((a, b) => {
      for (const k of keys) {
        if (a[k] < b[k]) return -1
        if (a[k] > b[k]) return 1
      }
      return 0
    })
  },
  extend (target, ...srcs) {
    return Object.assign(target, ...srcs)
  },
  isEmpty (x) {
    if (x == null) return true
    if (Array.isArray(x) || typeof x === 'string') return x.length === 0
    return Object.keys(x).length === 0
  },
  trim (s) {
    return String(s).trim()
  },
  isString (s) {
    return typeof s === 'string'
  },
  isObject (s) {
    return s !== null && typeof s === 'object'
  }
}

// ---------------------------------------------------------------------------
// stub：moment（只支持 'MM-DD' / 'YY-MM-DD'）
// ---------------------------------------------------------------------------
const pad2 = (n) => String(n).padStart(2, '0')
const moment = (d) => ({
  format (f) {
    const dt = d === undefined ? new Date() : d instanceof Date ? d : new Date(d)
    if (Number.isNaN(dt.getTime())) return 'Invalid date'
    return f
      .replace('YY', String(dt.getFullYear()).slice(-2))
      .replace('MM', pad2(dt.getMonth() + 1))
      .replace('DD', pad2(dt.getDate()))
  }
})

// ---------------------------------------------------------------------------
// stub：Character / Weapon（扫描 refs resources 的 data.json）
// ---------------------------------------------------------------------------
function scanChars (game) {
  const dir = path.join(REFS, 'resources', `meta-${game}`, 'character')
  const map = new Map()
  for (const sub of fs.readdirSync(dir)) {
    const f = path.join(dir, sub, 'data.json')
    if (!fs.existsSync(f)) continue
    const data = JSON.parse(fs.readFileSync(f, 'utf8'))
    const entry = { game, data }
    map.set(data.name || sub, entry)
    if (!map.has(sub)) map.set(sub, entry)
  }
  return map
}

function scanWeapons (game) {
  const dir = path.join(REFS, 'resources', `meta-${game}`, 'weapon')
  const map = new Map()
  for (const typeDir of fs.readdirSync(dir)) {
    const typePath = path.join(dir, typeDir)
    if (!fs.statSync(typePath).isDirectory()) continue
    for (const sub of fs.readdirSync(typePath)) {
      const f = path.join(typePath, sub, 'data.json')
      if (!fs.existsSync(f)) continue
      const data = JSON.parse(fs.readFileSync(f, 'utf8'))
      const entry = { game, typeDir, data }
      map.set(data.name || sub, entry)
      if (!map.has(sub)) map.set(sub, entry)
    }
  }
  return map
}

const CHARS = { gs: scanChars('gs'), sr: scanChars('sr') }
const WEAPONS = { gs: scanWeapons('gs'), sr: scanWeapons('sr') }

const Character = {
  // 对应 Meta.matchGame：以传入 game 优先，跨游戏兜底
  get (name, game = 'gs') {
    const other = game === 'gs' ? 'sr' : 'gs'
    const hit = CHARS[game].get(name) || CHARS[other].get(name)
    if (!hit) return false
    const { game: g, data } = hit
    return {
      id: data.id,
      name: data.name,
      abbr: data.abbr,
      getData (arr) {
        const out = {}
        for (const part of arr.split(',')) {
          const [key, src] = part.split(':')
          if (key === 'star') out.star = data.star
          else if (key === 'name') out.name = data.name
          else if (key === 'abbr') out.abbr = data.abbr
          else if (key === 'img' && src === 'face') out.img = `meta-${g}/character/${data.name}/imgs/face.webp`
        }
        return out
      }
    }
  }
}

const Weapon = {
  get (name, game = 'gs') {
    const hit = WEAPONS[game].get(name)
    if (!hit) return false
    const { game: g, typeDir, data } = hit
    return {
      id: data.id,
      name: data.name,
      getData () {
        return {
          star: data.star,
          name: data.name,
          // 与 models/Weapon.js 的 abbr getter 一致：名字不超过 4 个字用全名
          abbr: data.name.length <= 4 ? data.name : data.abbr || data.name,
          img: `meta-${g}/weapon/${typeDir}/${data.name}/icon.webp`
        }
      }
    }
  }
}

// ---------------------------------------------------------------------------
// stub：Data.readJSON → tools/fixtures
// ---------------------------------------------------------------------------
const Data = {
  readJSON (p) {
    // /data/gachaJson/{qq}/{uid}/{type}.json → gs；/data/srJson/... → sr
    const m = p.match(/^\/data\/(gachaJson|srJson)\/(.+)\.json$/)
    const game = m[1] === 'gachaJson' ? 'gs' : 'sr'
    const file = path.join(FIXTURES, 'gacha', game, `${m[2]}.json`)
    if (!fs.existsSync(file)) return []
    return JSON.parse(fs.readFileSync(file, 'utf8'))
  }
}

// ---------------------------------------------------------------------------
// eval 原始 pool 数据与 GachaData.js
// ---------------------------------------------------------------------------
function evalDataFile (file, exportNames) {
  let code = fs.readFileSync(file, 'utf8')
  code = code.replace(/^import[^\n]*$/gm, '')
  code = code.replace(/^export\s+(?=(const|let|var)\b)/gm, '')
  const body = `${code}\nreturn { ${exportNames.join(', ')} }`
  return new Function(body)()
}

const { poolDetail, mixPoolDetail } = evalDataFile(
  path.join(REFS, 'resources', 'meta-gs', 'info', 'pool.js'),
  ['poolDetail', 'mixPoolDetail']
)
const { poolDetailSr } = evalDataFile(
  path.join(REFS, 'resources', 'meta-sr', 'info', 'index.js'),
  ['poolDetailSr']
)

function loadGachaData () {
  let code = fs.readFileSync(path.join(REFS, 'apps', 'gacha', 'GachaData.js'), 'utf8')
  code = code.replace(/^import[^\n]*$/gm, '')
  code = code.replace(/^export default GachaData\s*$/gm, '')
  const body = `${code}\nreturn GachaData`
  return new Function(
    'lodash', 'Data', 'Character', 'Weapon', 'poolDetail', 'mixPoolDetail', 'poolDetailSr', 'moment',
    body
  )(lodash, Data, Character, Weapon, poolDetail, mixPoolDetail, poolDetailSr, moment)
}

const GachaData = loadGachaData()

// ---------------------------------------------------------------------------
// 跑用例（与 tools/verify_py.py 的 CASES 一致）
// ---------------------------------------------------------------------------
const CASES = [
  ['analyse', 1, '100000001', 301, 'gs'],
  ['analyse', 1, '100000001', 302, 'gs'],
  ['analyse', 1, '100000001', 200, 'gs'],
  ['analyse', 1, '100000001', 500, 'gs'],
  ['analyse', 2, '800000001', 11, 'sr'],
  ['analyse', 2, '800000001', 12, 'sr'],
  ['analyse', 2, '800000001', 1, 'sr'],
  ['analyse', 9, '999999999', 301, 'gs'],
  ['stat', 1, '100000001', 'char', 'gs'],
  ['stat', 1, '100000001', 'up', 'gs'],
  ['stat', 1, '100000001', 'normal', 'gs'],
  ['stat', 1, '100000001', 'mix', 'gs'],
  ['stat', 1, '100000001', 'all', 'gs'],
  ['stat', 2, '800000001', 'up', 'sr'],
  ['stat', 2, '800000001', 'char', 'sr'],
  ['stat', 2, '800000001', 'weapon', 'sr'],
  ['stat', 2, '800000001', 'normal', 'sr'],
  ['stat', 2, '800000001', 'all', 'sr'],
  ['stat', 2, '800000001', 'mix', 'sr']
]

const jsOut = {}
for (const [kind, user, uid, type, game] of CASES) {
  const key = `${kind}|${user}|${uid}|${type}|${game}`
  jsOut[key] = kind === 'analyse' ? GachaData.analyse(user, uid, type, game) : GachaData.stat(user, uid, type, game)
}
fs.writeFileSync(path.join(ROOT, 'tools', 'js_out.json'), JSON.stringify(jsOut, null, 1))

// Python 版
execSync('poetry run python tools/verify_py.py', { cwd: ROOT, stdio: 'inherit' })
const pyOut = JSON.parse(fs.readFileSync(path.join(ROOT, 'tools', 'py_out.json'), 'utf8'))

// ---------------------------------------------------------------------------
// 规范化 + 逐字段 diff
// 已知类型差异（不算不一致）：Python dict int key → JSON 后变字符串（天然对齐）；
// Python None ↔ JS undefined/缺失；stat items 的 id 在 JS 里是字符串、Python 保持 meta 原始类型
// ---------------------------------------------------------------------------
function canon (v, key) {
  if (v === false || v === null || v === undefined) return null
  if (Array.isArray(v)) return v.map((x) => canon(x))
  if (typeof v === 'object') {
    const out = {}
    for (const k of Object.keys(v).sort()) {
      if (v[k] === undefined || v[k] === null) continue
      out[k] = canon(v[k], k)
    }
    return out
  }
  if (key === 'id' && typeof v === 'string' && /^\d+$/.test(v)) return Number(v)
  return v
}

let fail = 0
for (const key of Object.keys(jsOut)) {
  const a = JSON.stringify(canon(jsOut[key]))
  const b = JSON.stringify(canon(pyOut[key]))
  if (a === b) {
    console.log(`OK   ${key}`)
  } else {
    fail++
    console.log(`DIFF ${key}`)
    fs.writeFileSync(path.join(ROOT, 'tools', `diff_${key.replaceAll('|', '_')}_js.json`), JSON.stringify(canon(jsOut[key]), null, 1))
    fs.writeFileSync(path.join(ROOT, 'tools', `diff_${key.replaceAll('|', '_')}_py.json`), JSON.stringify(canon(pyOut[key]), null, 1))
  }
}
console.log(fail === 0 ? `\n全部 ${Object.keys(jsOut).length} 个用例一致` : `\n${fail} 个用例不一致（diff 文件已写入 tools/）`)
process.exit(fail === 0 ? 0 : 1)
