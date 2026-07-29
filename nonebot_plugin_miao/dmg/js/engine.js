// 伤害计算编排：改编 refs/miao-plugin/models/ProfileDmg.js 的 calcData（'dmg' 模式），
// 并为改编版 DmgBuffs/DmgAttr 注入 Meta / ArtifactSet / Weapon 依赖。
// 输入（__miao_run 的 payload）由 Python 侧 dmg/service.py 组装：
//   game/level/cons/sp/uid/characterName/talent/trees/attr(面板+staticAttr)/
//   element(角色元素)/weaponTypeName/weapon{name,affix,level}/weaponTables/
//   sets(套装{名:2|4})/attrMap/enemyLv/idx(手动序号, 可空)
// 规则（角色 calc.js、武器类型 calc.js、圣遗物 calc.js）由 service.py 在 eval
// 本文件之后、调用 __miao_run 之前求值并放入全局：
//   __charRule {buffs,details,defParams,defDmgIdx,defDmgKey,mainAttr,enemyName}
//   __weaponBuffs（武器类型 calc.js 的返回表）  __artiBuffs（圣遗物 calc.js 的 default）

var Meta = {
  getMeta: function (game, type) {
    if (type === 'arti') return { attrMap: __attrMap || {}, artiBuffs: __artiBuffs || {} }
    return {}
  }
}

// ArtifactSet 薄 shim：套装遍历（>=4 件先生效 2 件套）与套装 buff 查表
var ArtifactSet = {
  eachSet: function (idxs, fn, game) {
    lodash.forEach(idxs || {}, function (v, k) {
      if (v >= 4) fn({ name: k }, 2)
      fn({ name: k }, v)
    })
  },
  getArtisSetBuff: function (name, num, game) {
    var artiBuffs = Meta.getMeta(game || 'gs', 'arti').artiBuffs
    var ret = (artiBuffs[name] && artiBuffs[name][num]) || artiBuffs[name + num]
    if (!ret) return false
    if (lodash.isPlainObject(ret)) return [ret]
    return ret
  }
}

// Weapon 薄 shim：移植 models/Weapon.js 的 getWeaponAffixBuffs
// （buff 表来自武器类型 calc.js 的求值结果 __weaponBuffs，精炼数值表来自 payload.weaponTables）
var Weapon = {
  get: function (name, game) {
    if (!name || typeof __weaponBuffs === 'undefined') return false
    var buffs = __weaponBuffs[name]
    if (!buffs) return false
    return {
      name: name,
      getWeaponAffixBuffs: function (affix, isStatic) {
        var list = Array.isArray(buffs) ? buffs : [buffs]
        var tables = {}
        lodash.forEach(__weaponTables || {}, function (ds, idx) { tables[idx] = ds[affix - 1] })
        var ret = []
        lodash.forEach(list, function (ds) {
          if (lodash.isFunction(ds)) ds = ds(tables)
          if (!ds) return true
          if (!!ds.isStatic !== !!isStatic) return true
          if (ds.isStatic) {
            var tmp = {}
            if (ds.idx && ds.key) {
              if (!tables[ds.idx]) return true
              tmp[ds.key] = tables[ds.idx]
            }
            if (ds.refine) {
              lodash.forEach(ds.refine, function (r, key) { tmp[key] = r[affix - 1] * (ds.buffCount || 1) })
            }
            if (!lodash.isEmpty(tmp)) ret.push({ isStatic: true, data: tmp })
            return true
          }
          // 自动拼接标题
          if (!/：/.test(ds.title)) ds.title = name + '：' + ds.title
          ds.data = ds.data || {}
          if (ds.idx && ds.key) {
            if (!tables[ds.idx]) return true
            ds.data[ds.key] = tables[ds.idx]
          } else if (ds.refine) {
            lodash.forEach(ds.refine, function (r, key) {
              ds.data[key] = function (args) { return r[args.refine] * (ds.buffCount || 1) }
            })
          }
          ret.push(ds)
          return true
        })
        return ret
      }
    }
  }
}

function __miao_run (payload) {
  var game = payload.game
  var isGs = game === 'gs'
  var rule = __charRule || {}
  var details = rule.details || false
  if (!details) return JSON.stringify({ error: 'no_rule' })

  var meta = {
    characterName: payload.characterName,
    uid: payload.uid,
    level: payload.level,
    cons: payload.cons * 1,
    talent: payload.talent,
    trees: payload.trees,
    weapon: payload.weapon
  }

  // getCalcRule 的默认值（ProfileDmg.js L102-109）
  var defParams = rule.defParams || {}
  var defDmgIdx = rule.defDmgIdx || -1
  var defDmgKey = rule.defDmgKey || ''
  var mainAttr = rule.mainAttr || 'atk,cpct,cdmg'
  // 原样保留原版的运算符优先级行为（cfg.enemyName || this.isGs）? '小宝' : '弱点敌人'
  var enemyName = (rule.enemyName || isGs) ? '小宝' : '弱点敌人'

  defDmgKey = lodash.isFunction(defDmgKey) ? defDmgKey(meta) : defDmgKey
  defDmgIdx = lodash.isFunction(defDmgIdx) ? defDmgIdx(meta) : defDmgIdx
  defParams = lodash.isFunction(defParams) ? (defParams(meta) || {}) : defParams

  var charStub = { weaponTypeName: payload.weaponTypeName, elem: payload.element, sp: payload.sp }
  var originalAttr = DmgAttr.getAttr({ weapon: payload.weapon, attr: payload.attr, char: charStub, game: game })

  var profile = { weapon: payload.weapon, artis: payload.sets }
  var buffs = DmgBuffs.getBuffs(profile, rule.buffs || [], game)

  var msg = DmgAttr.calcAttr({ originalAttr: originalAttr, buffs: buffs, artis: payload.sets, meta: meta, params: defParams, game: game }).msg

  var ret = []
  var detailMap = []
  var msgList = []

  // 用户手动输入伤害序号（calcData L160-163：先自减再兜底 0）
  var dmgIdx = payload.idx ? payload.idx * 1 : 0
  var idxIsInput = !!payload.idx
  if (idxIsInput) {
    dmgIdx = --dmgIdx < 0 ? 0 : dmgIdx
  }

  lodash.forEach(details, function (detail, detailSysIdx) {
    if (lodash.isFunction(detail)) {
      // 原版此处不传 params/game（ProfileDmg.js L181），保持原样
      var r0 = DmgAttr.calcAttr({ originalAttr: originalAttr, artis: payload.sets, buffs: buffs, meta: meta })
      var ds0 = lodash.merge({ talent: meta.talent }, DmgAttr.getDs(r0.attr, meta))
      detail = detail(Object.assign({}, ds0, { attr: r0.attr, profile: profile }))
    }
    if (!detail || detail.isStatic) return
    if (detail.cons && meta.cons < detail.cons * 1) return

    var params = lodash.merge({}, defParams, lodash.isFunction(detail.params) ? detail.params(meta) : (detail.params || {}))
    var r = DmgAttr.calcAttr({ originalAttr: originalAttr, buffs: buffs, artis: payload.sets, meta: meta, params: params, talent: detail.talent || '', game: game })
    var attr = r.attr

    var ds = lodash.merge({ talent: meta.talent }, DmgAttr.getDs(attr, meta, params))
    ds.artis = payload.sets
    if (detail.check && !detail.check(ds)) return

    var dmg = DmgCalc.getDmgFn({ ds: ds, attr: attr, level: payload.level, enemyLv: payload.enemyLv, showDetail: detail.showDetail, game: game })

    if (detail.dmg) {
      var basicDmgRet = detail.dmg(ds, dmg)
      detail.userIdx = detailMap.length
      detailMap.push(detail)
      var row = { title: lodash.isFunction(detail.title) ? detail.title(ds) : detail.title }
      lodash.forEach(basicDmgRet, function (v, k) { row[k] = v })
      ret.push(row)
    }
    msgList.push(r.msg)
  })

  // mode 'dmg' 的选中逻辑（ProfileDmg.js L217-227）
  var detail
  if (idxIsInput && detailMap[dmgIdx]) {
    detail = detailMap[dmgIdx]
  } else if (idxIsInput) {
    return JSON.stringify({
      error: 'idx',
      message: '序号输入错误：' + payload.characterName + '最多只支持' + detailMap.length + '种伤害计算哦'
    })
  } else if (!lodash.isUndefined(defDmgIdx) && details[defDmgIdx]) {
    detail = details[defDmgIdx]
  } else {
    detail = detailMap[0]
  }

  if (lodash.isFunction(detail)) {
    var rf = DmgAttr.calcAttr({ originalAttr: originalAttr, buffs: buffs, artis: payload.sets, meta: meta })
    var dsf = lodash.merge({ talent: meta.talent }, DmgAttr.getDs(rf.attr, meta))
    detail = detail(Object.assign({}, dsf, { attr: rf.attr, profile: profile }))
  }
  var basicRet = lodash.merge({}, ret[detail.userIdx] || ret[defDmgIdx])
  // 原版 dmgCfg.userIdx 为 detail.userIdx || defDmgIdx（ProfileDmg.js L237），
  // userIdx=0 是 falsy 会落到 defDmgIdx，原样保留该行为
  var userIdx = detail.userIdx || defDmgIdx

  // 属性增减矩阵（ProfileDmg.js L242-278）
  var dmgRet = []
  var attrMap = Meta.getMeta(game, 'arti').attrMap
  var mainAttrArr = mainAttr.split(',')
  var params2 = lodash.merge({}, defParams, detail.params || {})
  lodash.forEach(mainAttrArr, function (reduceAttr) {
    var rowData = []
    lodash.forEach(mainAttrArr, function (incAttr) {
      if (incAttr === reduceAttr) {
        rowData.push({ type: 'na' })
        return
      }
      var rr = DmgAttr.calcAttr({
        originalAttr: originalAttr, buffs: buffs, artis: payload.sets, meta: meta,
        params: params2, incAttr: incAttr, reduceAttr: reduceAttr, talent: detail.talent || '', game: game
      })
      var ds2 = lodash.merge({ talent: meta.talent }, DmgAttr.getDs(rr.attr, meta, params2))
      var dmg2 = DmgCalc.getDmgFn({ ds: ds2, attr: rr.attr, level: payload.level, enemyLv: payload.enemyLv, game: game })
      if (detail.dmg) {
        var cell = detail.dmg(ds2, dmg2)
        cell.type = cell.avg === basicRet.avg ? 'avg' : (cell.avg > basicRet.avg ? 'gt' : 'lt')
        rowData.push(cell)
      }
    })
    dmgRet.push(rowData)
  })

  var selMsg = msgList[idxIsInput ? dmgIdx : (defDmgIdx > -1 ? defDmgIdx : 0)] || msg

  return JSON.stringify({
    ret: ret,
    msg: selMsg,
    selectedIdx: userIdx,
    selected: basicRet,
    dmgRet: dmgRet,
    mainAttr: mainAttr,
    enemyName: enemyName,
    detailTitle: basicRet.title || (lodash.isFunction(detail.title) ? '' : detail.title)
  })
}
