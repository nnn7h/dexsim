import logging
import re
from time import clock

from smaliemu.emulator import Emulator
from timeout3 import TIMEOUT_EXCEPTION

from ..plugin import Plugin

__all__ = ["TEMPLET_PLUS"]

logger = logging.getLogger(__name__)


android_strs = [
    'Ljava/lang/System;', 'Landroid/os/Environment', 'Ljava/lang/String;->'
]


class TEMPLET_PLUS(Plugin):
    '''
    自动匹配解密插件

    用于匹配那些比较麻烦的解密方法，相对比较耗时——有时候，会超级耗时
    '''
    name = "TEMPLET_PLUS"
    enabled = True
    tname = None

    def __init__(self, driver, smalidir):
        Plugin.__init__(self, driver, smalidir)
        self.emu2 = Emulator()

    def run(self):
        print('Run ' + __name__, end=' ', flush=True)
        logger.info('running')

        start = clock()
        self.proccess()
        finish = clock()
        print('\n%fs' % (finish - start))

    def proccess(self):
        line_ptn = (
            r'invoke-static.*?{([v\.\d,\s]*)}, (.*?);->(.*?)'
            r'\(((?:B|S|C|I|J|F|D|Ljava/lang/String;|'
            r'\[B|\[S|\[C|\[I|\[J|\[F|\[D|\[Ljava/lang/String;'
            r')*?)\)Ljava/lang/String;')
        prog_line = re.compile(line_ptn)
        proto_ptn = (
            r'(B|S|C|I|J|F|D|Ljava/lang/String;|'
            r'\[B|\[S|\[C|\[I|\[J|\[F|\[D|\[Ljava/lang/String;)'
        )
        prog_proto = re.compile(proto_ptn)

        arr_data_prog = re.compile(self.ARRAY_DATA_PATTERN)

        move_result_obj_ptn = r'move-result-object ([vp]\d+)'
        move_result_obj_prog = re.compile(move_result_obj_ptn)

        for sf in self.smalidir:
            for mtd in sf.get_methods():
                # if 'xgtRtRawcet' not in str(mtd):
                #     continue
                # print(mtd)
                # 如果存在数组
                array_data_content = []
                arr_res = arr_data_prog.search(mtd.get_body())
                if arr_res:
                    array_data_content = re.split(r'\n\s', arr_res.group())

                lines = re.split(r'\n\s*', mtd.get_body())

                old_body = lines.copy()  # 存放原始方法体
                new_body = []   # 存放解密后的方法体

                snippet = []  # 存放smali代码
                args = {}   # 存放方法参数，用于smaliemu执行

                index = -1  # 用于计数

                for line in lines:
                    snippet.append(line)
                    index += 1

                    if 'invoke-static' not in line:
                        new_body.append(line)
                        continue

                    flag = False
                    # TODO 排除Android自身的类
                    for clz in android_strs:
                        if clz in line:
                            flag = True
                            break
                    if flag:
                        new_body.append(line)
                        continue

                    result = prog_line.match(line)
                    if not result:
                        new_body.append(line)
                        continue

                    # register_name, class_name, mtd_name, protos
                    # ('v1, v2, v3', 'Lcom/game/pay/sdk/y', 'a', 'ISB')
                    # 解密参数的寄存器名
                    rnames = result.groups()[0].split(', ')
                    cname = result.groups()[1][1:].replace('/', '.')
                    mname = result.groups()[2]
                    protos = prog_proto.findall(result.groups()[3])

                    # 初始化所有寄存器
                    del snippet[-1]
                    snippet.extend(array_data_content)
                    try:
                        args.update(self.pre_process(snippet))
                    except TIMEOUT_EXCEPTION:
                        pass

                    try:
                        registers = self.get_vm_variables(
                            snippet, args, rnames)
                        args = registers if registers else args
                    except TIMEOUT_EXCEPTION:
                        snippet.clear()
                        new_body.append(line)
                        continue

                    # 已经执行过的代码，不会执行
                    # emu 执行后，self.emu.vm.variables 的值是一直保存的
                    snippet.clear()

                    if not registers:
                        new_body.append(line)
                        continue

                    # 从寄存器中获取对应的参数
                    # 参数获取 "arguments": ["I:198", "I:115", "I:26"]}
                    arguments = []
                    # args = {}  # the parameter of smali method
                    ridx = -1
                    for item in protos:
                        ridx += 1
                        rname = rnames[ridx]
                        if rname not in registers:
                            break
                        value = registers[rnames[ridx]]
                        argument = self.convert_args(item, value)
                        if argument is None:
                            break
                        arguments.append(argument)

                    if len(arguments) != len(protos):
                        new_body.append(line)
                        continue

                    json_item = self.get_json_item(cname, mname,
                                                   arguments)
                    # {id}_{rtn_name} 让这个唯一化，便于替换
                    old_content = '# %s' % json_item['id']

                    # 如果 move_result_obj 操作存在的话，解密后一起替换
                    find = move_result_obj_prog.search(lines[index + 1])

                    if find:
                        rtn_name = find.groups()[0]
                        # 为了避免 '# abc_v10' 替换成 '# abc_v1'
                        old_content = old_content + '_' + rtn_name + 'X'
                        self.append_json_item(json_item, mtd, old_content,
                                              rtn_name)
                    else:
                        old_content = old_content + '_X'
                        self.append_json_item(
                            json_item, mtd, old_content, None)
                    # print(mtd)
                    old_body[index] = old_content

                mtd.set_body('\n'.join(old_body))

            self.optimize()
            self.clear()
