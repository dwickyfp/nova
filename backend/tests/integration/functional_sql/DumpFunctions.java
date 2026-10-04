import java.lang.reflect.*;
import java.util.*;

public class DumpFunctions {
    static Object call(Object target, String name) throws Exception {
        return target.getClass().getMethod(name).invoke(target);
    }
    static String type(Object value) throws Exception {
        String cls = value.getClass().getSimpleName();
        if (cls.equals("ArrayType")) return "ARRAY<" + type(call(value,"getItemType")) + ">";
        if (cls.equals("MapType")) return "MAP<" + type(call(value,"getKeyType")) + "," + type(call(value,"getValueType")) + ">";
        if (cls.equals("StructType")) {
            List<String> fields = new ArrayList<>();
            for (Object field : (Iterable<?>) call(value,"getFields")) fields.add(call(field,"getName") + " " + type(call(field,"getType")));
            return "STRUCT<" + String.join(",",fields) + ">";
        }
        if (cls.equals("AnyElementType")) return "ANY_ELEMENT";
        if (cls.equals("AnyArrayType")) return "ANY_ARRAY";
        if (cls.equals("AnyMapType")) return "ANY_MAP";
        if (cls.equals("AnyStructType")) return "ANY_STRUCT";
        return call(value,"getPrimitiveType").toString();
    }
    public static void main(String[] args) throws Exception {
        Object set = Class.forName("com.starrocks.catalog.FunctionSet").getConstructor().newInstance();
        call(set, "init");
        List<Map<String,Object>> result = new ArrayList<>();
        for (Object fn : (Iterable<?>) call(set, "getBuiltinFunctions")) {
            Map<String,Object> row = new LinkedHashMap<>();
            row.put("name", call(fn,"functionName"));
            row.put("kind", fn.getClass().getSimpleName());
            row.put("fid", call(fn,"getFunctionId"));
            List<String> inputs = new ArrayList<>();
            for (Object type : (Object[]) call(fn,"getArgs")) inputs.add(type(type));
            row.put("arguments", inputs);
            row.put("return_type", type(call(fn,"getReturnType")));
            row.put("varargs", call(fn,"hasVarArgs"));
            row.put("visible", call(fn,"isUserVisible"));
            Object state = call(fn,"getAggStateDesc");
            if (state != null) {
                Map<String,Object> descriptor = new LinkedHashMap<>();
                descriptor.put("name", call(state,"getFunctionName"));
                List<String> arguments = new ArrayList<>();
                for (Object input : (Iterable<?>) call(state,"getArgTypes")) arguments.add(type(input));
                descriptor.put("arguments",arguments);
                descriptor.put("return_type",type(call(state,"getReturnType")));
                row.put("state_descriptor",descriptor);
            }
            List<?> catalog = (List<?>) fn.getClass().getMethod("getInfo", boolean.class).invoke(fn,true);
            row.put("catalog",catalog.subList(0,4));
            result.add(row);
        }
        Object gson = Class.forName("com.google.gson.Gson").getConstructor().newInstance();
        System.out.println(gson.getClass().getMethod("toJson", Object.class).invoke(gson,result));
    }
}
