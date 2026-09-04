cmake_minimum_required(VERSION 3.20)
project(PTOASLModelStaticCxxConsumer LANGUAGES CXX)

find_package(PTOASLModel 0.1 CONFIG REQUIRED)

add_executable(pto_asl_model_static_cpp_consumer main.cpp)
target_link_libraries(
    pto_asl_model_static_cpp_consumer
    PRIVATE PTOASLModel::pto_asl_model_static)

enable_testing()
add_test(
    NAME pto_asl_model_static_cpp_consumer
    COMMAND pto_asl_model_static_cpp_consumer)
