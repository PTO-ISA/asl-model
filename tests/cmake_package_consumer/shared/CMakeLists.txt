cmake_minimum_required(VERSION 3.20)
project(PTOASLModelSharedCConsumer LANGUAGES C)

find_package(PTOASLModel 0.1 CONFIG REQUIRED)

add_executable(pto_asl_model_shared_c_consumer main.c)
target_link_libraries(
    pto_asl_model_shared_c_consumer
    PRIVATE PTOASLModel::pto_asl_model)

enable_testing()
add_test(
    NAME pto_asl_model_shared_c_consumer
    COMMAND pto_asl_model_shared_c_consumer)
