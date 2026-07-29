// QuickJS 沙箱前置 stub：为改编版引擎（dmg_*.js）与上游规则文件
// （角色/武器/圣遗物 calc.js）提供 lodash / Format / console 的最小实现。
// 与 verify_dmg.mjs 中的 stub 保持一致；Format.elemName 复刻原版
// components/common/Elem.js 的行为（只查 gs 元素映射）。

var console = { log: function () {} }

var lodash = {
  forEach: function (obj, fn) {
    if (Array.isArray(obj)) obj.forEach(function (v, i) { fn(v, i) })
    else Object.keys(obj || {}).forEach(function (k) { fn(obj[k], k) })
  },
  concat: function () { return Array.prototype.concat.apply([], Array.prototype.slice.call(arguments)) },
  filter: function (arr, fn) { return (arr || []).filter(fn) },
  isPlainObject: function (x) { return x !== null && typeof x === 'object' && !Array.isArray(x) },
  isFunction: function (x) { return typeof x === 'function' },
  isString: function (x) { return typeof x === 'string' },
  isUndefined: function (x) { return typeof x === 'undefined' },
  isEmpty: function (x) {
    if (x === null || typeof x === 'undefined') return true
    if (Array.isArray(x) || typeof x === 'string') return x.length === 0
    return Object.keys(x).length === 0
  },
  sortBy: function (arr, keys) {
    if (!Array.isArray(keys)) keys = [keys]
    return arr.slice().sort(function (a, b) {
      for (var i = 0; i < keys.length; i++) {
        var k = keys[i]
        if (a[k] < b[k]) return -1
        if (a[k] > b[k]) return 1
      }
      return 0
    })
  },
  merge: function (target) {
    target = target || {}
    for (var i = 1; i < arguments.length; i++) {
      var src = arguments[i]
      if (!src) continue
      Object.keys(src).forEach(function (k) {
        var v = src[k]
        if (lodash.isPlainObject(v)) target[k] = lodash.merge(lodash.isPlainObject(target[k]) ? target[k] : {}, v)
        else target[k] = v
      })
    }
    return target
  }
}

var Format = (function () {
  var elemAlias = {
    anemo: '风,蒙德', geo: '岩,璃月', electro: '雷,电,雷电,稻妻', dendro: '草,须弥',
    pyro: '火,纳塔', hydro: '水,枫丹', cryo: '冰,至冬'
  }
  var elemMap = {}
  Object.keys(elemAlias).forEach(function (key) {
    elemMap[key] = key
    elemAlias[key].split(',').forEach(function (t) { elemMap[t] = key })
  })
  return {
    pct: function (num, fix) { if (fix === undefined) fix = 1; return (num * 1).toFixed(fix) + '%' },
    comma: function (num, fix) {
      if (fix === undefined) fix = 0
      return String(parseFloat((num * 1).toFixed(fix)))
    },
    percent: function (num, fix) { return Format.pct(num * 100, fix) },
    // 原版 Elem.elem/elemName 只使用 gs 映射（sr 元素名靠中文别名恰好命中）
    elem: function (elem, defElem) {
      return elemMap[String(elem || '').toLowerCase()] || (defElem === undefined ? '' : defElem)
    },
    elemName: function (elem, defName) {
      var key = Format.elem(elem)
      return key ? elemAlias[key][0] : (defName === undefined ? '' : defName)
    }
  }
})()
